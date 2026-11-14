# NanoJev: 21 zero-centered Gaussian banks (V1)

This patch adds **five additional finer Gaussian banks** to the existing
16-bank numerical-first V3 experiment. It leaves the original 16-bank V3 code,
checkpoints, and `stage2_snapshot.pt` unchanged. Install the ZIP contents with
`main_computer_test/` mapped to the repository root (`main_computer/`).

## Fixed geometry

The first bank is centered at zero with radius 2,000,000. Each successive bank
halves that radius. All banks use the upstream Stage 2 Gaussian `registers`
count (129 in the production checkpoint) **plus two dedicated overflow
registers**. For out-of-range numerical oracle values, 100% of the target is
in the appropriate negative/positive overflow bucket. All 21 banks train in
parallel with the numerical-first loss from V3; there is no bank-by-bank
prediction replacement.

| Bank | Stage key | Radius | Bin spacing at 129 registers |
|---:|---:|---:|---:|
| 16 | 18 | 61.03515625 | 0.95367431640625 |
| 17 | 19 | 30.517578125 | 0.476837158203125 |
| 18 | 20 | 15.2587890625 | 0.2384185791015625 |
| 19 | 21 | 7.62939453125 | 0.11920928955078125 |
| 20 | 22 | 3.814697265625 | 0.059604644775390625 |
| 21 | 23 | 1.9073486328125 | 0.0298023223876953125 |

## Direct decoder and backwards-compatible initialization

The 16-bank V3 head uses a `LayerNorm` over a fixed-size concatenated input.
Appending five banks to that exact normalization layer would change the old
head's behavior immediately, even with zero new weights. Instead:

1. Copy the first 16 trained banks and the entire original V3 direct decoder
   into a **trainable** 16-bank branch of the new 21-bank head.
2. Initialize five new banks with the same zero-centered Gaussian/overflow
   supervision, at half-width radii.
3. Add a **trainable 21-bank direct decoder branch** taking *all 21 distributions*
   and estimates. Its last layer starts at exactly zero.
4. Add the two direct signed-asinh corrections. The old V3 numerical output is
   **exactly preserved at step zero**, before updates. Both branches and all
   21 banks participate in subsequent optimization.

The import is strictly read-only and checks the old checkpoint's schema,
source/teacher provenance hashes, bank count, upstream frozen state, geometry,
and hidden dimension. The imported optimizer state is deliberately discarded.

## Run: first 1,000 CUDA training steps

From `C:\Users\subsi\main_computer` with your usual `$py` variable:

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_joint_decoder_21 `
    --device cuda `
    --relative-max 2000000 `
    --decoder-init-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_joint_decoder_16_v3\latest.pt" `
    --additional-steps 1000 `
    --max-steps 30000000 `
    --chunk-steps 1000 `
    --views 32 `
    --gate-percent 40
```

New checkpoint output: `C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_joint_decoder_21_v1\latest.pt`.
The logged `joint_prepare` must report `bank_count: 21`, `step: 0` and a non-null
`decoder_init_sha256`. The step-zero dev metrics should reproduce the V3 donor
checkpoint's predictions (assuming the same frozen inputs and 16-bank weights).

For another 1,000 steps, issue the same command with `--additional-steps 1000`.
The new run resumes itself; `--decoder-init-checkpoint` remains unchanged.

## Testing

```text
python -m unittest discover -s tests -p "test_nanojev_hawk_dove_gaussian_joint_decoder_21.py" -v
```

The targeted tests cover fixed support, 129+2 register geometry, 100% overflow,
exact migrated prediction preservation, gradients through 21 banks, sampler,
train/dev probes, warm-start validation, mocked CPU training/resume, and the
test-split access boundary.

## Important: the 90%-within-90% prompt generator is NOT in this patch

This patch only expands the *model's numerical resolution*. It uses the existing
2,304-example training source and the existing magnitude-balanced sampler.
The data distribution has not been changed to the proposed nested 90% rule,
and it does not synthesize new oracle-certified Hawk-Dove examples. For truly
new information below 1, the prompt generator and teacher oracle must be
extended separately. If few/no train examples fall inside Bank 21's radius,
the new bank cannot learn that interior mapping merely from additional steps.
Even with 21 banks, 5%-relative precision for values arbitrarily near zero
cannot be guaranteed by a finite-width Gaussian register grid.

## Verification boundary

Tested on CPU with mocked external feature loading; not trained on the user's
CUDA GPU. Initial accuracy is inherited from V3 by design, **not guaranteed to
improve** solely from the five added supports. Keep the original V3 run for
comparison, and inspect the new `bank_train`, `bank_dev`, and
`magnitude_band_counts` diagnostics before a long run.
