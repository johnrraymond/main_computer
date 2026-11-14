# NanoJev multi-tap / recurrent feedback laboratory

Independent laboratory; it **does not modify** any existing HybridAgent trainer, source model, or checkpoint.

## Causal hypothesis

A frozen Gaussian backbone may have useful numeric information in its first
hidden activation, second hidden activation, pre-Gaussian location logits,
and/or its population-bank distribution **before** 256-bin density decoding.
A learned HybridLink-like sidecar might combine these representations and feed
its own latent thought **back into the frozen second backbone layer**, improving
X/Y accuracy over the original backbone, a single-step trained sidecar, and
an equally trained four-step sidecar that only re-enters the backbone once.

**No final X/Y predictions are supplied to the feedback loop.** The only
inference inputs are the source model's six parsed visible operands. Ground-truth
products, partial sums, and X/Y are used solely as TRAIN readout targets or
final TRAIN numerical supervision; they are never sidecar input tokens.

## Tap semantics

| Tap | Source | Dimension for a 128-hidden/2000-bank model |
| --- | --- | --- |
| `early` | Frozen reference `SiLU(encoder[0](features))` | 128 |
| `late` | Frozen reference `SiLU(encoder[2](early))` | 128 |
| `prehead` | Frozen reference `encoder[4](late)` logits, **before** sigmoid and bank construction | 2 |
| `bank` | Softmax weights over the 2000 Gaussian banks, grouped into 16 geometry-based buckets per X/Y output, **before** density projection | 32 |

The last two are head-adjacent *representations*, not hidden transformer layers.
The bank tap is a compressed diagnostic of the population; it is not the final
density or numeric X/Y readout. No predictions/labels from the dataset are
used to construct any tap.

## Experiments

1. Linear ridge readouts trained on unique TRAIN instances for six raw operands,
   four products, and two normalized partial sums. Evaluate R² and normalized
   MAE on independent-matrix DEV. Compare `early`, `late`, `prehead`, `bank`,
   `early+late`, `late+prehead`, and `all`. Linear readouts are diagnostics only.
2. Independently train the sidecar at four thought steps for every tap subset,
   holding pretrained Gaussian reference weights constant. All arms initialize
   *every* sidecar module and trainable answer interface identically; only the
   active input taps differ. Predictions use a direct normalized X/Y output
   (no Gaussian 256-bin density transformation) to avoid decoder confounding.
3. Train `all_depth1`, `all_depth2`, and `all` (depth 4), each for **equal
   optimizer steps**, identical initial weights and minibatch draws. This is a
   matched *training* comparison, not simply truncating a four-step model.
4. Train `all_nofeedback`: four sidecar thoughts with the same modules,
   optimizer budget and final readout, but a **single** re-entry to the backbone
   after the fourth thought, rather than re-entering at every thought. The
   sidecar still has its own recurrent token memory. This isolates iterative
   *backbone reinjection*, not recurrence in the sidecar itself.
5. On the selected best DEV checkpoint, independently zero and distinct-instance
   shuffle each selected tap, reporting paired rescued/regressed cases, all-mask
   joint X/Y within 5%, and numerical prediction changes. DEV shuffle operates
   over entire split and preserves pairing of both rendered masks. If a
   compressed tap's numerical values are indistinguishable, that individual
   shuffle is explicitly marked UNVERIFIED rather than claimed as valid.
6. Evaluate the original frozen Gaussian and its pre-bank direct locations as
   separate controls on identical DEV instances. Save original SHA-256 hashes
   and refuse on checkpoint/config identity drift.

## Certification criteria

`MULTITAP_CAUSAL_FEEDBACK=PASS` requires **all** of the following on DEV:

- Depth-4 `all` model improves by >=2 percentage points in strict all-mask
  per-tier X/Y joint-within-5% rate over separately trained `all_depth1`.
- It likewise beats separately trained `all_depth2` by >=2 points.
- It likewise beats the equally trained `all_nofeedback` by >=2 points.
- It likewise beats the immutable original Gaussian baseline by >=2 points.
- At least one intervention on the depth-4 `all` inputs produces positive net
  paired rescues over the intervention (not merely prediction movement).

`NOT_ESTABLISHED` is a valid scientific result. A nonlinear sidecar with more
parameters than a pure reference is not evidence for latent reasoning unless
it beats the controlled alternatives. Trained arms use DEV selection and no
TEST at all by default; only `--final-test` evaluates TEST after freezing
all arms. TEST results must never be used to select arms, architectures, or
learning rates. Prior experiments already used this DEV split, so this run is
exploratory, not untouched blind certification.

## Install

```text
python new_patch.py nanojev_hybrid_multitap_feedback_lab_patch.zip --dry-run
python new_patch.py nanojev_hybrid_multitap_feedback_lab_patch.zip
```

## Run (Windows, Python venv)

```text
python -m tools.nanojev_hawk_dove_hybrid_multitap_lab --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_hybrid_agent_v4_distinct_shuffle" --output "C:\Users\subsi\NanoJev\runs\hawk_dove_hybrid_multitap_lab_v1" --device cuda --steps 1500 --probe-every 250 --batch-size 128
```

The CLI runs all seven tap configurations, separately trained depth-1 and
-2 `all` variants, and the four-step final-only-reentry control. Every arm
is checkpointed at each probe and can resume deterministically. The
`--resume` option resumes all incomplete arms, and `--stop-after N` pauses
inside the current arm for recovery testing. Use a **new output directory**;
existing results are refused if `--resume` is absent.

For quick CPU smoke testing, select only `--arms early,all_depth1,all_depth2,all,all_nofeedback`
and reduce `--steps`, `--batch-size`, and `--probe-every`. Excluded arms are
not evaluated or silently treated as passed.

## Output layout

- `config.json`: verified source SHA256, teacher SHA256, matrix hashes, args.
- `activation_readouts.json`: TRAIN-fitted DEV readouts per tap combination.
- `reference_controls.json`: untouched Gaussian reference DEV outputs.
- `ARM/latest.pt`: atomic resumable model/optimizer/RNG and train history.
- `ARM/best.pt`: DEV-selected model; selection frozen prior to TEST.
- `ARM/done.json`: DEV accuracy at depths 0/1/2/4, tap ablations, history.
- `report.json`: cross-arm comparisons and overall causal feedback status.

The original reference's first linear encoder is *not* made token-aware:
re-entry is into its **first hidden activation before the second hidden
layer**, which is the concrete integration boundary currently available.
This is a controlled HybridLink-like experiment, **not** the full published
HybridAgent architecture, nor proof of independent self-discovered arithmetic
thoughts. No oracle intermediate products are injected in the loop.
