# NanoJev 2,000-bank oracle-blind matrix-held-out certification

## Invariant

**An inference input may contain only the opponent's Hawk/Dove counts and the four explicitly bound payoff values; oracle X/Y values are withheld from every inference tier. TRAIN matrices and DEV matrices are entirely disjoint from TEST matrices. Every certified tier must reach at least 80% of independent matrix/count cases with BOTH outputs within 5% of the correct nonzero magnitude, across every rendered mask. An exact-zero target requires an exact-zero prediction.**

This experiment is separate from, and does not overwrite, the original fixed-payoff 2,000-bank checkpoint.

## Why T00 is excluded

Canonical T00 has both raw oracle answers and no counts. Removing its oracles leaves insufficient information to calculate X/Y. Therefore the new gate explicitly certifies **T01–T08**, not T00. Canonical T01 has raw X, and canonical T02 has raw Y; the new variants **T01B/T02B** replace those numerical oracle branches with `COMPUTE_X` / `COMPUTE_Y`. They are transparently redefined blinded variants, not claims that canonical T01/T02 were oracle-free. T03–T08 retain their source-tier expression/prose forms with a four-payoff binding header.

## What the model learns

The network receives two opponent-count fractions, four numerical payoff bindings and a tier indicator extracted from **its own prompt**, then generates two Gaussian densities with 2,000 shared fixed basis banks. The optimizer trains X/Y distributions using TRAIN oracle labels. It does not read row metadata or X/Y labels as inputs. The model uses the same Gaussian accumulation mechanism as the conditional baseline.

This is a **structured numeric-operation generalization** test, not a claim of raw-token grammar or Lisp/English transformation comprehension. T03–T08 still share the same 6 numerical input features after parsing; the one-hot tier differs.

## Default CUDA training

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_population_blind_matrix train `
    --banks 2000 `
    --device cuda `
    --steps 15000 `
    --probe-every 500 `
    --train-matrices 96 `
    --dev-matrices 24 `
    --test-matrices 24 `
    --counts-per-matrix 16 `
    --masks 2 `
    --tolerance 0.05 `
    --min-rate 0.80 `
    --output "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1"
```

All four payoff values are integers sampled without replacement from `[-4,4]`, forming **144 distinct matrices** with fixed seed. The matrix identities are assigned to only one split. Within a matrix, 16 counts are tested (including extremes and available boundary/zero-root probes). Each tier has 2 representation masks; success is counted by **matrix/count instance**, and both representations must pass. Split matrix SHA256 hashes are persisted in `matrix_split_manifest.json`. All data is generated reproducibly from a saved seed and parameters; no teacher SQLite database is required for this experiment.

Default scope: 96 train matrices, 24 DEV matrices, 24 withheld TEST matrices, 16 opponent-count cases per matrix, eight tiers, two masks. This generates 24,576 TRAIN presentations, 6,144 DEV presentations, and 6,144 TEST presentations. DEV chooses the best checkpoint according to the weakest tier's joint case rate (then the average tier rate, then density TV). TEST is evaluated **once**, after the best checkpoint is frozen, and cannot promote the checkpoint.

The exact pass rule is `abs(pred - truth) <= 0.05 * abs(truth)` independently for X and Y; BOTH must pass on the SAME instance and on BOTH masks. Zero outputs get zero permissible error. This is deliberately harder than the previous fixed-payoff gate. Output `status: FAIL` is a valid scientific result. It does not mean the program failed to run.

## Independent read-only audit

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_population_blind_matrix audit `
    --checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1\best.pt" `
    --splits dev,test `
    --device cuda `
    --output "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1\certification.json"
```

The audit regenerates the saved matrix split deterministically, verifies its identity against the checkpoint, and scores the selected frozen checkpoint without training. It rejects checkpoints from the original fixed-payoff model.

## Standalone prediction

Prepare one oracle-blind prompt file with a binding header and a T01–T08 body in the training format. Then run:

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_population_blind_matrix predict `
    --checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1\best.pt" `
    --tier T08 `
    --mask 0 `
    --prompt-file "C:\path\to\blind_prompt.txt" `
    --device cuda
```

This subcommand has no oracle reference or database input and rejects noncanonical prompts.

## Limits

This test does not measure arbitrary payoff matrices outside the declared `[-4,4]` integer range, nor does it establish general language or symbolic execution. `T01B`/`T02B` are new counterfactual tiers. Frozen Gaussian positions and widths are retained, and the relative numerical gate is stringent near zero. It can fail even when density TV is low. There is no checkpoint resume; choose a new output directory for each independent run. Do not adjust training based on TEST failures and then treat those same TEST matrices as untouched holdout data.
