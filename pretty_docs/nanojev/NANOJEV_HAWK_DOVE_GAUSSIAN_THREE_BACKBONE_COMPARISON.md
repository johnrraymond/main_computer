# Hawk–Dove: verified Gaussian baseline plus independent frozen-backbone corrections

The first triad experiment failed at 0% because both integration variants replaced
the original Gaussian head's known-working 14 prompt-derived numerical inputs
with frozen hidden activations. This is a **different task** from the reproduced
60.94% parsed-input reference; changing only the adapter cannot restore the
reference contract.

This corrected experiment uses the **same original pretrained Gaussian head**
for all three runs and trains a *separate residual correction* from each LM:

- `qwen` — Qwen3-0.6B
- `tinystories` — TinyStories-33M
- `pythia` — Pythia-70M

`Visible canonical prompt -> 14 count/payoff/tier features -> frozen original Gaussian head`

`Same visible prompt -> frozen LM -> independent trainable location residual -> Gaussian head`

The original `BlindDensityNetwork` Gaussian banks, proposal and gains are
unchanged, initialized from the checkpoint, and **frozen**. A residual network
applies a bounded adjustment to the reference head's X/Y location proposals.
Its last layer starts at zero; therefore, at step 0, the complete predicted
density is bitwise equal to the reference head's prediction for the same
prompt-derived numerical features. A same-device DEV equality check refuses
training if this property fails. Each backbone retains separate residual
parameters, optimization, captured activations, and checkpoints.

**These runs certify increment over the structured numeric binding baseline,
not zero-shot comprehension of numbers from raw text.** Read the `dev_backbone_increment`
field alongside `dev_no_backbone_min_joint5` in each summary. If the increment
is zero, the backbone did not improve the original numerical solution.

## Canonical invocation

Use the ORIGINAL replay checkpoint, **not** one of the failed triad checkpoints:

```shell
python -m tools.nanojev_hawk_dove_gaussian_three_backbone train-all \
  --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" \
  --reuse-cache-root "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_three_backbone_v2_direct" \
  --device cuda --banks 2000 --grid-bins 256 --hidden 128 \
  --steps 15000 --probe-every 500 --seed 20261022 \
  --train-matrices 96 --dev-matrices 24 --test-matrices 24 \
  --counts-per-matrix 16 --masks 2 --tolerance 0.05 --min-rate 0.80 \
  --output "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_three_backbone_v3_bound"
```

The optional `--reuse-cache-root` verifies and reuses any **FP32** hidden-state
cache from the preceding direct-input run, including the exact ordered prompt
hashes and model identity. Missing per-backbone caches are captured normally.
Older FP16 or mismatched caches are refused. On a 6 GB GPU, use the default
sequential run, **not** `--parallel`.

Before loading an LM, the harness validates the original checkpoint's schema,
all training configuration, exact train/dev/test matrix identities and DEV
baseline score. For the official 2000-bank / 256-bin / 15000-step baseline it
also requires >=50% weakest-tier DEV. It does not read TEST until after the
new independent residual is frozen. During `probe(0)` it checks the actual
Gaussian output against the reference on the *same compute device* and writes
the reference-equivalent candidate to `best.pt`.

## Outputs and limits

Each backbone writes its own `feature_cache.pt`, `config.json`,
`matrix_split_manifest.json`, `history.json`, `best.pt`, and `summary.json`.
The top-level `comparison.json` compares independent DEV/TEST rates across
identical matrices. To audit a saved checkpoint:

```shell
python -m tools.nanojev_hawk_dove_gaussian_three_backbone audit \
  --checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_three_backbone_v3_bound\qwen\best.pt" \
  --device cuda --splits dev,test
```

The original reference checkpoint must remain available when starting training,
but read-only audits load the frozen baseline weights from each triad checkpoint.
The original 80%-within-5% joint X/Y matrix-held-out certification is unchanged.
The underlying Qwen/TinyStories/Pythia model weights remain frozen. Successful
reference parity is *not* proof that the LM contributed useful numerical skill;
the independent increment is the relevant measure.
