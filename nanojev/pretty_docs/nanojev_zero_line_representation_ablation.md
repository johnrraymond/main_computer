# NanoJev frozen-representation ablation: absolute magnitude 192–223

This is a standalone, readout-only diagnostic. It does **not** train a gate, modify the
certified backbone, modify previously accepted banks, or promote a new stage.

All three arms see **identical signed-addition training examples**, DEV cards,
and genuinely held-out maximum-operand magnitudes within absolute band 192–223.
Each bank has the same two-hidden-layer MLP topology with trainable parameter
counts matched within 2%, the same loss and learning rate, and identical
training-example selection. The best weights are selected by DEV exact accuracy.
OOD labels do **not** participate in training or checkpoint selection.

Arms:

* `backbone`: recurrent final-state features from certified radius-127 network.
* `augmented`: recurrent features **plus** its frozen input digit-token embeddings.
* `token_only`: frozen pretrained input digit-token embeddings, **no** recurrent features.

`token_only` is an ablation of the *recurrent representation*, **not** a random
initialization or an untrained input embedding. If its performance matches
`augmented`, the recurrent features add little in this setup; this alone does
not show that the zero-line curriculum was useless.

**No routing:** each arm independently predicts the full signed seven-digit
answer; no true residual, previous answer, correctness indicator, or oracle
bank choice can enter the prediction. Correct sums exist only as training
labels and scoring labels. The learned digits/sign embeddings are generated
from the raw operands by the frozen pretrained input layer.

Example command (from the repo root, after applying this patch):

```powershell
& $py -m tools.nanojev_zero_line_representation_ablation `
  --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
  --baseline-stage "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_bank_autopilot_adaptive_v2\stages\192_223" `
  --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_representation_ablation_192_223_v1" `
  --device cuda `
  --steps 6000 `
  --probe-every 250 `
  --batch-size 128
```

The `--baseline-stage` is optional. If provided, the runner checks its band
and certified-source hash before comparing the previously trained 192–223 bank.
The base checkpoint SHA-256 is rechecked at completion.

The output directory has `config.json`, `result.json`, and per-arm `best.pt`
and `training.pt` files. Re-running with the same arguments resumes the saved
optimizer and sampler states; changing training parameters in place is refused.
Use a **different output directory** for different hyperparameters.

Reports: exact TRAIN and DEV, withheld magnitude-fold OOD (within 192–223),
and a separate adjacent-magnitude 224–255 stress test. Each includes zero,
near-zero, intermediate, frontier and far residual breakdowns. Adjacent
accuracy is **extrapolation**, not a replacement for certified generalization.
The heldout sets have already influenced earlier research; reserve a completely
new untouched test for any eventual scientific certification.

Interpretation:

- `augmented > backbone`: input-token information assists decoding beyond
  the frozen recurrent state.
- `token_only >= augmented`: the pretrained input representation may be
  sufficient; additional recurrent state is not demonstrably necessary.
- All poor on the adjacent band: additional readout capacity isn't enough;
  extending the backbone itself or changing the curriculum deserves testing.

This experiment makes **no changes to autopilot promotion criteria** and
cannot silently add banks to the accepted chain.
