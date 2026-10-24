# NanoJev V3: learned chi-squared magnitude-bank population

**Invariant:** X/Y magnitude is decoded from the *network-learned unsigned
bank probabilities* and gamma bank analytical means, not from a bypass scalar
proposal. Sign and exact zero are independently classified.

The independent module `tools.nanojev_hawk_dove_chi_population_blind_matrix`
keeps the V2 2,000-bank magnitude geometry (1,195 centers below 1 by default,
`1e-5` through 5 million), gamma/chi-squared kernel shapes, and negative /
exact-zero / positive classifications. Unlike V2, the network outputs two
**2,000-dimensional learned logit vectors** plus two three-way sign vectors.
Softmax yields two full-bank activations. The model's predicted unsigned
magnitude is the weighted analytical mean of the activated gamma banks.
There is no scalar magnitude/log-magnitude prediction head. The 512-bin gamma
density is the same learned mixture used for the numerical expectation.

Supervision uses TRAIN-only oracle labels for sign cross entropy, a two-bank
barycentric target distribution for the learned bank logits, logarithmic
numerical error on the population's actual expected value, and gamma density
KL. Oracle values are never passed as inference inputs. TEST is never used to
update or select a checkpoint. DEV alone chooses `best.pt`.

The original oracle-blind prompt parser, fixed payoff-matrix-disjoint split
manifest, and strict 80% of independent matrix/count cases with **both X and
Y within 5% across all masks** for each T01B–T08 tier are unchanged. This
experiment cannot certify fractional outputs because its current oracle
payoffs and opponent counts are integers; sub-1 kernels nevertheless remain
part of the requested general-purpose representation.

V2 and legacy Gaussian source/checkpoints remain unchanged; V3 has its own
schema, program name, and run directory. Output collision is refused.

## CUDA launch

Run the following as one line within an activated Python environment:

```text
python -m tools.nanojev_hawk_dove_chi_population_blind_matrix train --banks 2000 --grid-bins 512 --min-mag 0.00001 --density-power 1.65 --device cuda --steps 20000 --probe-every 500 --train-matrices 96 --dev-matrices 24 --test-matrices 24 --counts-per-matrix 16 --masks 2 --tolerance 0.05 --min-rate 0.80 --output C:\Users\subsi\NanoJev\runs\hawk_dove_chi_population_2000_blind_matrix_v3
```

Then audit the frozen checkpoint:

```text
python -m tools.nanojev_hawk_dove_chi_population_blind_matrix audit --checkpoint C:\Users\subsi\NanoJev\runs\hawk_dove_chi_population_2000_blind_matrix_v3\best.pt --device cuda --splits dev
```

And diagnose bank activation concentration and reconstruction (without
changing the checkpoint):

```text
python -m tools.nanojev_hawk_dove_chi_population_blind_matrix diagnose --checkpoint C:\Users\subsi\NanoJev\runs\hawk_dove_chi_population_2000_blind_matrix_v3\best.pt --device cuda --splits dev --output C:\Users\subsi\NanoJev\runs\hawk_dove_chi_population_v3_dev_diagnosis
```

`dev_cases.csv` now adds each output's top bank index, its positive magnitude
center, probability, and effective bank count. Low accuracy with many banks
active exposes population diffusion, as opposed to mispredicted sign/zero.

## Tests and limitations

The tests prove the default bank geometry, exact gamma normalization,
reference population ability to reconstruct `1e-5` through `4e6`, dependency
of numerical predictions on learned bank activations, gradient propagation,
exact-zero behavior, a five-example end-to-end optimization covering zero,
small signed numbers and millions, and train/audit/predict/diagnose behavior.

**A five-example fit or CPU smoke is not certification.** Whether 20,000 CUDA
steps on the held-out matrix problem improves on the approximately 61% signed
Gaussian DEV baseline must be measured, not assumed. The unsigned gamma TV
is not numerically comparable to the signed Gaussian grid TV.
