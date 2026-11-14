# NanoJev — sampled signed-residual backbone ladder

## Purpose

Continue the **same recurrent NanoJev backbone** after the ±32 research
milestone. Expand the signed residual radius geometrically, without creating
banks, specialist heads, inference-time integer rules, or magnitude routers.

At every step both operands remain in `[-2_000_000, +2_000_000]` and labels
exist only inside the trainer/evaluator. The model is given only `(a, b)`.
All possible sums are in `[-4_000_000, +4_000_000]`.

## Starting point and provenance

`nanojev_zero_line_signed_radius32_v1/latest.pt` (step 2,750) is a **research
checkpoint**, NOT a formally promoted one. Its sealed ±32 holdout achieved
20,270/20,280 exact with ten nonzero errors, no zero errors, and 65,025/65,025
historical retention; the old harness refused formal promotion because a
cell had two errors. This ladder verifies the original result and related
config/checkpoint/source hashes. It never changes, reopens, or retroactively
promotes the old run. The original radius-127 checkpoint, successful zero-only
checkpoint, and ±16 data must still be present.

## Geometric plan

`64, 128, 256, 512, 1_024, 2_048, 4_096, 8_192, 16_384, 32_768,
65_536, 131_072, 262_144, 524_288, 1_048_576, 2_097_152, 4_000_000`

For each radius, every integer in `[-32, +32]` is explicitly included in the
**evaluation** at eligible operand magnitudes. Training cycles through all
1..32 magnitudes in both signs, with extra zero rehearsal and stochastic
sampling of the outer interval. The rest of the interval, `32 < |δ| <= radius`,
is sampled by log-scale residual bins. The sampler only produces **valid pairs**
whose operands are within ±2,000,000, including same-sign ordinary addition
when `|δ|` is large. In particular, there is no false assumption that a large
sum can be produced by operands in a small-magnitude band.

When extending to radius 4,000,000, the exact pair `(+2M,+2M)` and its
negative require the ±2M endpoint operand. That endpoint was already used
as a diagnostic, remains outside TRAIN/DEV, and is separately evaluated as
an endpoint stress case at the final stage.

## Commands

From the repository root in `.venv-diagram`, or the active environment with
PyTorch CUDA support:

```powershell
& $py -m tools.nanojev_zero_line_sampled_radius_ladder --phase plan --target 4000000
```

First, perform **one** attempted expansion (±64) before authorizing a full
campaign:

```powershell
& $py -m tools.nanojev_zero_line_sampled_radius_ladder `
  --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
  --zero-only-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_zero_only_v1" `
  --r16-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_signed_radius16_v1" `
  --r32-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_signed_radius32_v1" `
  --output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_sampled_radius_ladder_v1" `
  --device cuda `
  --target 4000000 `
  --max-new-stages 1 `
  --steps-per-stage 15000 `
  --probe-every 250 `
  --batch-size 128
```

If ±64 is accepted, rerun the **same command** with `--resume` and
`--max-new-stages 17` to permit the remaining geometric stages. A completed
stage is loaded from its frozen promoted checkpoint and verified against its
SHA record. A stage still in training can resume with `--resume`; it is
permissible to increase `--steps-per-stage` on resume. All other training,
evaluation, architecture and provenance settings are pinned in `config.json`.

## Acceptance (explicitly experimental)

Each stage requires three consecutive qualifying DEV probes across two
disjoint DEV magnitude views. A full, exhaustive historical audit must retain
65,025/65,025. Then it seals and evaluates one TEST dataset **once**.
Promotion requires:

- Canonical exact zero on all sampled zero-line examples.
- At least 99.9% exact for nonzero residuals with `1 <= |δ| <= 32`.
- At least 90% of outer residuals within **5% relative error**.
- At least 80% within 5% in each sufficiently populated operand-band or
  logarithmic residual bucket (`>=16` examples).
- At least 99% correct signed result for nonzero examples.
- Retention of original exact arithmetic and the earlier zero-only rejection
  checks. The ±2M endpoint is independently checked, including a far-residual
  5% check when attempting the full ±4M sum radius.

Per-category counts, worst buckets, counterexamples, and accuracy are written
to `stages/radius_<R>/result.json`. No one may infer success from a global
average if a prerequisite fails. Every checkpoint is a frozen **research
milestone**, not a proof of correctness on all integers.

The former ±16 and ±32 TEST magnitudes are permanently excluded from new
TRAIN/DEV, as is the ±2M endpoint. The new ladder uses a fixed, embargoed
magnitude TEST partition across **multiple stages**. Its first unopened test
is fresh relative to this ladder; later stage TEST results are *not independent
pristine magnitude-family certificates* because the same held-out families
are visited at increasing radii. Do not use prior stage TEST failures to
choose training examples, revise hyperparameters for that stage, or retry a
failed stage against the same sealed TEST. Arrange a separate final test with
new magnitude families for a final claim of broad arithmetic generalization.

## Files and failure behavior

- `config.json`: exact source provenance, code hashes and hyperparameters.
- `manifest.json`: current cumulative status and accepted stage list.
- `stages/radius_<R>/latest.pt`: optimizer/RNG/model state for a resumable
  unfinished stage.
- `stages/radius_<R>/best_dev.pt`: DEV-selected reference.
- `stages/radius_<R>/promoted.pt` and `acceptance.json`: model frozen at the
  accepted research stage, with provenance SHA.
- `stages/radius_<R>/test_opened.json`: irreversible sealing, whether the TEST
  passed or failed. **A failed TEST cannot be reused through `--resume`.**

The test never feeds the true residual to the model; Python arithmetic supplies
training targets and computes accuracy. Do not deploy these research
checkpoints as a verified arithmetic engine.
