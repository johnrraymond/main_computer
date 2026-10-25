# NanoJev zero-line bit-radius ladder (experimental v1)

## Governing objective

Train the existing `AdditionNet('digit_tokens')` recurrent neural backbone to preserve signed displacement across the additive zero line. For any supported pair `(A, B)`, the model independently predicts `delta = A+B`. Equivalent representations may look different internally, but must give the same answer: `(A+0)+(B+0)`, `(B+A)`, and `(A+k)+(B-k)`.

This is a neural-learning experiment. `A+B` is used to construct labels and evaluate the model, never as an inference executor. No router, hand-coded carry, arithmetic expert, Z output, or task-specific Hawk–Dove features are supplied.

## Complete expanding decks

| Radius | Operand domain | Complete ordered deck |
|---:|---|---:|
| 1 | -1…1 | 9 |
| 3 | -3…3 | 49 |
| 7 | -7…7 | 225 |
| 15 | -15…15 | 961 |
| 31 | -31…31 | 3,969 |
| 63 | -63…63 | 16,129 |
| 127 | -127…127 | 65,025 |

A rung enumerates every `(A,B)` card and its exact signed residual. Half of each training batch advances a shuffled exhaustive sweep through every card. The other half is stratified by zero, negative/positive near-zero, and negative/positive away-from-zero, so exact balance does not become too rare at larger radii. Each card is scored exactly; the learner cannot pass by predicting zero everywhere.

The first rung is *exposed mastery*, not a held-out generalization experiment. The same is true for each rung after its cards have been introduced. Memorization is permitted and deliberately measured. The genuinely novel measure is performance on the **new outer shell before training on that shell**. At radius 3 there are 40 novel cards; the 9 radius-1 cards remain in the combined 49-card deck.

## Composition and gates

The neural training batches include `(A,B)`, swapped or balanced-translated equivalent pairs, and neutral-element probes `(A,0)` and `(0,B)`. Targets are generated from the known arithmetic identities. A small consistency penalty encourages equivalent pairs to produce the same output distributions, while supervised signed and digit targets prevent trivial constant outputs. This does **not** require the internal representations of A-bar and B-bar to be identical.

At each rung a checkpoint advances only if:

1. The **entire** current deck is 100% exact, including predicted sign and decimal digits (not just 5% relative accuracy).
2. Every represented zero/near/away stratum is 100% exact.
3. All sampled commutativity, balanced-shift, A+0, and B+0 composition checks are 100% correct.
4. The complete deck has been visited during training.
5. These conditions persist for three consecutive spaced probes (configurable).

Earlier radii remain within later complete decks; current full-deck mastery enforces their retention. A failed rung stops progression. The runner saves `latest.pt` (resumable), `radius_<R>_best.pt` (best observed), and `radius_<R>_passed.pt` only for truly passing weights.

## Reproducible CUDA run

Run from the Main Computer repository root. The module depends on the existing `tools.nanojev_addition_curriculum` model class from Progressive Addition v2.

```powershell
& $py -m tools.nanojev_zero_line_ladder `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --device cuda `
    --max-radius 31 `
    --steps-per-rung 3000 `
    --probe-every 100 `
    --stability-probes 3 `
    --batch-size 128
```

Default training initializes a **fresh instance of the same recurrent digit-token backbone** so learning the nine-card world can be observed rather than presumed. This does not update the original Hawk–Dove backbone. For a separate transfer-arm comparison, add `--init-checkpoint "...\\digit_tokens\\signed_passed.pt"`; this is optional and must use a different output folder.

Resume a failed rung by using the *same* options and output folder, increasing the total step budget and adding `--resume`:

```powershell
& $py -m tools.nanojev_zero_line_ladder `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --device cuda `
    --max-radius 31 `
    --steps-per-rung 6000 `
    --probe-every 100 `
    --stability-probes 3 `
    --batch-size 128 `
    --resume
```

`--max-radius` can likewise increase on resume, using the same trained weights. All other identity-relevant settings must remain unchanged. If an older run has completed its requested final radius, use `--resume` with a larger `--max-radius` to extend it.

## Artifacts and interpretations

- `config.json`: source/dependency fingerprints and settings; prevents incompatible resume.
- `rungs/radius_R.json`: current exact mastery, behavioral composition, stable gate, and the **pre-training shell score** if applicable.
- `rungs/radius_R_best_map.csv`: every `(A,B)` coordinate, expected and predicted residual, exactness and stratum at the selected best checkpoint.
- `rungs/radius_R_passed_map.csv`: final complete two-dimensional mapping at passing checkpoint.
- `rungs/radius_R_passed.pt`: safe frozen reference for a mastered radius.
- `summary.json`: requested-radii result; explicitly says no TEST or HybridLink certification.

Pre-training shell probes are diagnostic DEV observations, **not** independent TEST certification. Beyond the first rung, the shell report is computed before that shell becomes part of training. A 100% rung proves full memorization, not generalization; only high *pre-training* accuracy on subsequent shells supports transfer of the learned relationship. The representation is decimal digit-token based, while the domain grows according to bit-radius boundaries; that does not imply a binary-encoded internal representation. 

This experiment is isolated from the existing addition curriculum, the signed checkpoint, and Hawk–Dove. It never evaluates a final TEST split, writes to original checkpoint paths, or certifies HybridLink.
