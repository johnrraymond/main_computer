# HybridAgent location-bypass diagnostic (read-only)

This independent diagnostic answers one question: **does the trained numerical
thought pathway already predict accurate X/Y locations that lose accuracy
when decoded via the 2,000-Gaussian population?** No HybridAgent trainer,
checkpoint, Gaussian code, or architecture is modified.

## Experiment and controls

The probe loads **the three saved best DEV-selected attention arms** from the
completed representation lab, verifying their checkpoint hashes, source
identities, and stored DEV metrics. For each arm on precisely the same DEV
presentations, it computes:

- **`direct`**: trained attention → its existing sigmoid-normalized two-number
  location → affine conversion into numeric X/Y. No retraining and no density.
- **`gaussian`**: *same trained location* → existing 2,000-bank density →
  existing 256-bin density-mean numerical decoding. The saved lab's DEV
  Gaussian result must replay (strict outcomes and counts exact; continuous
  error diagnostics tolerate small batched floating-point differences).
- **`oracle_direct`**: known X/Y → normalized locations → direct affine
  conversion. This quantifies numerical roundoff introduced by representing
  locations in float32, not learned accuracy.
- **`oracle_gaussian`**: *same true normalized locations* → fixed Gaussian
  banks/basis → density mean. This isolates the bank/basis decoding distortion,
  but is **not** a mathematical upper bound: learned locations may compensate
  for Gaussian bias.
- **`oracle_numeric`**: original exact DEV labels, an evaluation-only identity
  reference. It never enters a learned prediction path or training.

Every result includes the strict **all-masks, both X/Y within 5% relative**
criterion (exact zeros require exact equality); exact-zero/near-zero error
slices; DEV rescues and regressions by paired numerical instance and tier;
and numeric direct-vs-Gaussian shifts. TRAIN is not used for gradient updates;
TEST is not accessed.

## Install and run

Install the ZIP in the Main Computer repository via `new_patch.py` as normal.
Run using the same Python environment that ran the representation lab:

```text
python -m tools.nanojev_hawk_dove_hybrid_location_bypass_probe --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_hybrid_agent_v4_distinct_shuffle" --lab-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_hybrid_representation_lab_v1" --device cuda
```

The report is written to a **new** directory:

`C:\Users\subsi\NanoJev\runs\hawk_dove_hybrid_representation_lab_v1\location_bypass_probe_v1\report.json`

The diagnostic refuses to overwrite an existing output directory; if a
repeat is required, specify a fresh directory with `--output`.

## Interpretation

- Direct significantly better than Gaussian for the **same frozen learned
  locations**: population decoding loses accuracy. Check paired rescued vs.
  regressed cases, not just the minimum tier.
- Both direct and Gaussian poor, while oracle-direct succeeds: errors arise
  *before or at the trained numerical location head*, even though the
  Gaussian stage may add distortion.
- Oracle-Gaussian poor but learned Gaussian better than learned direct:
  the trained location head has likely learned to compensate for Gaussian
  distortion. An extra direct supervised head would need separate training;
  simple bypass is not yet justified.
- Oracle-direct poor, especially for exact zeros: float32 location roundoff
  itself interferes with exact-zero scoring. Direct-output readout should be
  tested before interpreting more architecture changes.

For all readings, supplied teacher products are computed from the six visible
numeric operands; no teacher target X/Y is supplied to the attention model.
Historical DEV scores are exploratory because DEV was inspected repeatedly
while designing the experiments. Do not claim TEST certification from this
probe, which deliberately leaves TEST untouched.
