# NanoJev addition transfer probe (v1)

## Objective

We have a `digit_tokens` model that passed atomic, no-carry, carry, and signed
addition in `nanojev_progressive_addition_v2`, but its next broad cancellation
stage mixed two distinct interventions: increased operand magnitude and
increased subtraction/cancellation difficulty. That model's latest cancellation
checkpoint is not used because it lost some signed retention.

This separate research runner loads the immutable **signed-stage passed**
checkpoint. Six independent models begin with precisely the same trained
weights. This is a *learned transfer* probe, not a Hawk–Dove certification.
Neither an arithmetic executor nor any teacher answer is used in inference.
The training generator computes ordinary addition only to create labels.

## Six independent branches

| Branch | Operand range and signs | DEV groups |
| --- | --- | --- |
| `positive_3digit` | Two positive operands, each 100–999 | no carry, carry |
| `positive_4digit` | Two positive operands, each 1,000–9,999 | no carry, carry |
| `opposite_3digit_away` | Opposite signs, both 100–999; difference ≥25% of larger magnitude | positive result, negative result |
| `opposite_4digit_away` | Opposite signs, both 1,000–9,999; difference ≥25% of larger magnitude | positive result, negative result |
| `near_4digit` | Opposite signs, both 1,000–9,999; strictly nonzero residual at most ~1% of magnitude | positive residual, negative residual |
| `zero_4digit` | Magnitudes 1,000–9,999; exact opposite operands | positive first, negative first |

The first two branches isolate length without negative operands. The next two
introduce subtraction without severe cancellation. The fifth isolates near
cancellation at fixed length. The sixth measures exact zero at fixed length,
**but easy zero-only mastery must not be mistaken for learned subtraction**:
zero-prediction frequency and false zeros are recorded separately, and near
and away branches are the decisive nonzero comparison.

Each branch has its own disjoint TRAIN and held-out DEV *unordered magnitude
pairs*. Operand order and sign mirrors share the same split; a repeated
magnitude with a different partner may appear in both splits. TEST values are
**never generated or evaluated** by this tool. The source checkpoint is never
modified. All six branches initialize from the same signed-stage weights,
not from another branch's final weights, so a failed stage cannot prevent the
remaining diagnostic comparisons.

## Strict acceptance and measurements

* Per branch: ≥80% strict relative 5% accuracy for **every** DEV category.
* Retention: 100% mastery of the exposed atomic table, and ≥80% on every
  category of the original `no_carry`, `carry`, and `signed` DEV sets.
* Every checkpoint probe reports TRAIN, DEV min, DEV overall, retention min.
  Detailed JSON records per-category counts, exact digit accuracy, continuous
  regression diagnostics, false zero counts, and separate retention results.
* Output is decoded exclusively from model-predicted sign and digits. The
  executor does not add operands. Predictions of zero pass only when the
  target is zero (the existing strict zero rule).
* Both trained weights and optimizer/sample RNG state are saved for resume.
  The best evaluated checkpoint is saved even when no acceptance gate passes.
  No branch result promotes a Hawk–Dove or HybridLink certification.

## Run on the user's machine

Unzip into your Main Computer repo root. This patch **adds** one runner, tests,
and documentation; it does not replace `nanojev_addition_curriculum.py`.
The earlier v2 patch must already be installed. The source model is the saved
*successful signed stage*, not `cancellation_best.pt` or `latest.pt`.

```text
python -m tools.nanojev_addition_transfer_probe --signed-checkpoint "C:\Users\subsi\NanoJev\runs\nanojev_progressive_addition_v2\arms\digit_tokens\signed_passed.pt" --output "C:\Users\subsi\NanoJev\runs\nanojev_addition_transfer_v1" --device cuda --steps 1500 --probe-every 250 --batch-size 128
```

To study one branch without running the others:

```text
python -m tools.nanojev_addition_transfer_probe --signed-checkpoint "C:\Users\subsi\NanoJev\runs\nanojev_progressive_addition_v2\arms\digit_tokens\signed_passed.pt" --output "C:\Users\subsi\NanoJev\runs\nanojev_addition_transfer_positive3_v1" --device cuda --branches positive_3digit --steps 1500 --probe-every 250 --batch-size 128
```

Increase the *total* number of steps per branch without reinitializing:

```text
python -m tools.nanojev_addition_transfer_probe --signed-checkpoint "C:\Users\subsi\NanoJev\runs\nanojev_progressive_addition_v2\arms\digit_tokens\signed_passed.pt" --output "C:\Users\subsi\NanoJev\runs\nanojev_addition_transfer_v1" --device cuda --steps 3000 --resume --probe-every 250 --batch-size 128
```

Only `--steps` may be increased when resuming. All other settings, selected
branches and the checkpoint fingerprint must remain identical. The source
curriculum code hash must also match the saved checkpoint's declared source.
If the existing source curriculum has changed, the runner refuses to launch
rather than silently interpreting its weights with a different architecture.

Outputs:

* `config.json` – model lineage, source SHA-256, experiment parameters.
* `branches/<name>/baseline.json` – evaluation **before further training**.
* `branches/<name>/best.json` and `best.pt` – best observed evaluation.
* `branches/<name>/latest.pt` – resumable model, optimizer and RNG state.
* `summary.json` – DEV category scores, retention, gate, and lineage.

This is not an automatic curriculum ladder: it is a **controlled, common-source
ablation** to identify whether length, opposite-sign arithmetic, or
cancellation is the limiting factor. Once isolated, a later curriculum may
train the stages sequentially and test full transfer back into Hawk–Dove.

## Tests

```text
python -m pytest -q tests/test_nanojev_addition_transfer_probe.py
```

Tests cover interval and group classification, sign mirrors, train/dev/test
pair isolation, source checkpoint validation, zero-shortcut prevention, strict
retention gating, no TEST usage during training, source immutability,
deterministic resumability, configuration drift refusal, and optional CUDA
decoder parity.
