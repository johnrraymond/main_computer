# NanoJev absolute-magnitude banks: protected zero-line expansion

## Why this is a separate experiment

Earlier `--intervals "16:32"` Gaussian/MLP experiments partitioned **by the truth of the answer**, `16 <= abs(A+B) <= 32`, and their learned router was trained with whether the original answer was wrong. This new module DOES NOT partition by the answer and does not reference the first answer when selecting a bank.

**This experiment partitions the INPUT space only:**

```text
M = max(abs(A), abs(B))
Bank 16:32    -> M in [16, 32]   (diagnostic only)
Bank 128:159  -> M in [128, 159] (first possible expansion beyond certified base)
Bank 160:191  -> M in [160, 191]
Bank 192:223  -> M in [192, 223]
Bank 224:255  -> M in [224, 255]
```

`M` is known from the two input operands before attempting addition. **At inference, neither the true sum, the original answer, an error label, a learned residual estimate, nor a Gaussian response is used for bank selection.** The independent bank produces the complete signed sum directly from frozen NanoJev recurrent activations. It is an ordinary MLP.

## Preserving the working zero line

* Load the immutable, stable radius-127 certified backbone and original heads from `rungs/radius_127_passed.pt`. Verify the complete **65,025-card** mastered deck at the beginning.
* Original source handles every input with `max(abs(A),abs(B)) <= 127`; bank outputs are **structurally bypassed** on that domain.
* A `16:32` bank can be trained and scored in isolation for an architecture comparison, but **can never replace or be promoted over the certified source**; its integrated prediction is the base prediction.
* For higher magnitude bands, the candidate replaces the base only for that absolute INPUT magnitude band. A promotion additionally requires preserving **every previously correct zero and ±1/±2 result** within that band, on both held-out DEV and held-out-magnitude OOD populations. This prevents expansion from trashing the learned zero line beyond radius 127.
* Existing promoted banks remain frozen. Candidate bank failure does not mutate or replace them, or the source.

## Train and measure exactly the absolute 16–32 experiment (diagnostic)

```powershell
& $py -m tools.nanojev_zero_line_absolute_magnitude_banks `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_absolute_bank_16_32_diagnostic_v1" `
    --bands "16:32" `
    --device cuda `
    --steps-per-bank 1500 `
    --probe-every 250 `
    --batch-size 128
```

This evaluates how an independent MLP learns the **absolute operand-magnitude interval** 16–32, *not* answers from ±16–32. Its integrated output must remain the certified original. Because the source backbone has already trained on every card in this region, the diagnostic DEV split is held out from **bank training**, not from the source backbone. It is not new knowledge certification.

## First genuine extension of the mastered space

```powershell
& $py -m tools.nanojev_zero_line_absolute_magnitude_banks `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_absolute_banks_128_255_v1" `
    --bands "128:159,160:191,192:223,224:255" `
    --device cuda `
    --steps-per-bank 1500 `
    --probe-every 250 `
    --batch-size 128
```

For an initial shorter experiment use only `--bands "128:159"` and a different output folder. This avoids consuming training time in bands that depend on a previous bank passing.

## Data and metrics

Training data is **all kinds of arithmetic within an absolute operand band**, not a residual-selected subset. Every generated pair has `low <= max(abs(A),abs(B)) <= high`; the correct sum `A+B` is used for supervised training labels and for test scoring only. TRAIN batches deliberately emphasize zero, ±1/±2, ±3..15, ±16..32 while retaining larger sums. This is label-balanced training, **not** answer-based bank routing.

* TRAIN and DEV are separated by unsigned magnitude-pair family: sign mirrors and operand swaps share a partition.
* **OOD** holds out entire input `M` values *within the bank's assigned range*, never sampled for TRAIN or DEV. This tests previously unseen absolute operand magnitudes while respecting each bank's ownership.
* **Next-magnitude diagnostic** tests an equal-width higher band, separately from OOD and without promoting from this diagnostic. Its input magnitude is *outside* the learned ownership interval and therefore should not be confused with the in-band OOD.
* Reports contain TRAIN/DEV/OOD accuracy and accuracy by residual bins (zero, 1–2, 3–15, 16–32, 33–64, >64), positive/negative answer, original-vs-bank changed predictions, and uninterrupted retention of the certified deck.
* Eligibility requires >=80% total isolated DEV and OOD, >=80% on the zero and ±16..32 diagnostic groups on both splits, zero regression on previously correct zero and ±1/±2 cards, and 100% original-deck retention. An in-base diagnostic bank can never be eligible. This is an exploratory acceptance gate, **not** a claim of full general arithmetic certification: per-residual exact certification requires additional future evaluation.
* Failing banks are saved as candidate checkpoints and never promoted. No external arithmetic executor, TEST access, or backbone update is involved.

## Output files

* `config.json`: source hash, absolute band assignments, settings.
* `bank_LOW_HIGH/candidate.pt`: immutable-source-bound trained specialist.
* `bank_LOW_HIGH/result.json`: full learning curve, baseline, isolated, integrated, retention and next-magnitude scores.
* `bank_LOW_HIGH/promoted.pt`: exists **only** if strict checks passed.
* `result.json`: summary across all bands and accepted intervals.

To run focused tests:

```text
python -m pytest -q tests/test_nanojev_zero_line_absolute_magnitude_banks.py
```
