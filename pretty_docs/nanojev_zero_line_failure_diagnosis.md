# NanoJev: 192–223 arithmetic-failure diagnosis

This is a **read-only** diagnostic of the already-trained representation-ablation
checkpoints. It does not retrain the backbone, select checkpoints, modify banks,
train a router, or promote any magnitude band. It examines the existing
**DEV-selected** three arms: `backbone`, `augmented`, and `token_only`, plus the
frozen original base and the existing specialist from the adaptive autopilot
stage when configured. All models are evaluated on **identical input cards**.

## Run

After installing the standalone patch at the `main_computer` root, use:

```powershell
& $py -m tools.nanojev_zero_line_failure_diagnosis `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --ablation-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_representation_ablation_192_223_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_failure_diagnosis_192_223_v1" `
    --device cuda
```

The runner discovers the original reference specialist checkpoint from the
ablation's `config.json` if the ablation was run with `--baseline-stage`; override
using `--baseline-stage` if necessary. To run on CPU use `--device cpu`.

## Outputs

- `result.json`: group-wise exact accuracy, error counts, mean absolute numeric
  error on failed cards, sign accuracy, between-model complementarity, and
  operand-transformation checks for DEV, magnitude-held-out OOD, and adjacent
  untrained 224–255 magnitude bands.
- `cards_ood.csv`, `cards_dev.csv`, `cards_adjacent.csv`: one row **per input**
  with signed operands, correct sum (evaluation only), predictions and exactness
  for every arm, signed residual class, cancellation versus ordinary addition,
  carry/borrow event count, operand orientation, 8-magnitude subband, and
  magnitude boundary category.
- `worst_backbone_buckets`: populous weak categories (at least 20 cards), sorted
  by exact accuracy, not by hand-picked examples.
- `largest_backbone_errors`: 25 concrete counterexamples with all three
  representation-arm predictions.
- `metamorphic`: exactness and prediction consistency for `(b,a)` and `(-a,-b)`.
  Invariance alone is **not** counted as arithmetic correctness.

### Classification details

The split between `opposite_sign_cancellation` and `same_sign_addition` comes
from the *input* operand signs, not from a predicted residual. For same-sign
operands, grade-school decimal **carry** events are counted while adding
magnitudes. For opposite-sign operands, decimal **borrow** events are counted
when subtracting the smaller magnitude from the larger. No events is a
separate category. Count labels are `none`, `single`, and `multiple`.

Residual classes: exact zero, ±1–2, ±3–15, ±16–32, ±33–64, and abs>=65. Both
signed-residual accuracy and operand orientation are reported. Boundary labels
separate the first and last three magnitudes of each evaluated band, the
199/200, 209/210, and 219/220 decimal transitions, and the remaining interior;
8-value magnitude bins additionally show finer location trends. Adjacent
224–255 is a **distinct extrapolation population**, not the in-band OOD fold.

### Source and inference integrity

- Loads the immutable radius-127-certified backbone and verifies its checkpoint
  SHA-256 against the ablation configuration and again after diagnosis.
- Reconstructs the ablation's exact deterministic DEV and withheld-magnitude OOD
  split from its original seed and per-cell configuration; mismatched counts,
  swapped checkpoint arms, provenance, or sample bands fail closed.
- Loads each `best.pt` selected during original training, **without** using
  diagnosis data to select a different checkpoint.
- Supplies only frozen operand-derived activations and pretrained token
  embeddings as network inputs. Gold sums, signed-residual groups, carries,
  decimal-boundary tags and bank IDs are **evaluation labels only**.
- Explicitly mutates labels on a test batch and verifies model outputs do not
  change. Operand swaps and sign flips are new forward passes through each arm.

**Scientific limitation:** These OOD and adjacent-band populations have already
informed research decisions in preceding runs. These analyses diagnose failure
mechanisms, not untouched final certification. Accuracy on small subgroups can
be noisy; interpret the group `cards` denominator, paired model comparisons,
and cross-population agreement. Source pretraining may still benefit the models;
this experiment only classifies where they fail.
