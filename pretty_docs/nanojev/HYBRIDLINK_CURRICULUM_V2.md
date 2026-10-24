# HybridLink Curriculum v2: four independently runnable phases

This is a separate experiment built on the **unchanged** v1 shared causal
backbone (`tools.nanojev_hawk_dove_hybridlink_sequence`). It does not overwrite
v1, multi-tap, v4, or the original Gaussian checkpoint. The Gaussian reference
remains a **frozen seed feature extractor**; the causal Transformer is already
trainable in format and joint phases. The goal is to establish teacher-thought
understanding **before** training HybridLink, then test generated inputs.

## Installation and commands (Windows Python)

Install the patch with `python new_patch.py ... --dry-run` followed by
`python new_patch.py ...` from the repository root.

Run the following four commands separately. Change the source checkpoint if
needed; use the same `--output` and all training/gate options for all phases.
The example uses one **recurrent** arm; add `--arms recurrent,static` to all
commands to run the matched static control as well.

```powershell
& $py -m tools.nanojev_hawk_dove_hybridlink_curriculum --phase format --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" --output "C:\Users\subsi\NanoJev\runs\hawk_dove_hybridlink_curriculum_v2" --device cuda --steps 2000 --probe-every 200 --batch-size 128
```

```powershell
& $py -m tools.nanojev_hawk_dove_hybridlink_curriculum --phase verify --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" --output "C:\Users\subsi\NanoJev\runs\hawk_dove_hybridlink_curriculum_v2" --device cuda
```

**Only after `CURRICULUM_FORMAT_GATE` prints `PASS`**:

```powershell
& $py -m tools.nanojev_hawk_dove_hybridlink_curriculum --phase alignment --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" --output "C:\Users\subsi\NanoJev\runs\hawk_dove_hybridlink_curriculum_v2" --device cuda --steps 800 --probe-every 200 --batch-size 128
```

**Only after `CURRICULUM_ALIGNMENT_GATE` prints `PASS`**:

```powershell
& $py -m tools.nanojev_hawk_dove_hybridlink_curriculum --phase joint --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" --output "C:\Users\subsi\NanoJev\runs\hawk_dove_hybridlink_curriculum_v2" --device cuda --steps 2400 --probe-every 200 --batch-size 128
```

**If format fails:** Train the *same phase* for additional steps, then verify:

```powershell
& $py -m tools.nanojev_hawk_dove_hybridlink_curriculum --phase format --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" --output "C:\Users\subsi\NanoJev\runs\hawk_dove_hybridlink_curriculum_v2" --device cuda --steps 4000 --resume --probe-every 200 --batch-size 128
```

`--steps` means the **total target steps within that phase**, not additional
steps. After an initial 2,000-step run, `--resume --steps 4000` performs 2,000
more. The checkpoint stores optimizer, sampling generator, and torch CPU/CUDA
RNG. The run identity excludes phase and step target but includes source SHA,
model dimensions, sampling hyperparameters, and gate thresholds. Changing these
requires a new output directory. Do not copy a checkpoint between outputs.

**If alignment fails:** extend alignment with `--resume --steps ...`, keeping
format proof unchanged. If joint is already started, upstream phases are locked.
The acceptance SHA is calculated over the **actual saved checkpoint bytes**;
modification to an upstream checkpoint invalidates downstream authorization.

## What each phase does

### Phase 1 — `format`

The causal backbone receives `[numeric_seed_1, numeric_seed_2,
teacher_product_1, ..., teacher_product_4]`. Both seed and teacher embeddings
are trainable. The last hidden state supervises normalized X/Y. An auxiliary
probe predicts a product from the state **after** its teacher token, while the
new **next-product probe** predicts product `k` from the state immediately
**before** teacher product `k` is consumed. These have separate losses and
separate DEV metrics. No teacher information is used at autonomous inference.

The `next_product` head is an independent readout with a supervised prefix
objective, not the original consumed-position value probe applied to the
wrong positions. Prefix causal isolation is regression-tested.

### Phase 2 — `verify`

Read-only forward evaluation of the saved `format/latest.pt` model on DEV;
no training, no TEST. Default acceptance checks:

* Minimum per-tier strict joint X/Y accuracy with **correct** teacher tokens
  at least **80%**.
* Next-product normalized MAE at most **0.08** and R² at least **0.80**.
* Consumed-position product normalized MAE at most **0.08**.

These are explicit research targets, not performance claims. If any check
fails, `gate.json` records `FAIL`, alignment refuses to start, and you should
inspect the printed values before deciding whether to extend phase 1.

### Phase 3 — `alignment`

The format checkpoint and accepted SHA are prerequisites. Freeze the causal
backbone, seed encoders, teacher-product embeddings, answer head, and product
readouts. Train **only** HybridLink (and the static arm's control-step tokens)
against the teacher input embeddings. Report input-embedding MSE/cosine, plus
**numerical** recovery from generated embeddings using the frozen consumed-
product readout. A distinct-product shuffled-target MSE control prevents high
cosine alone from passing.

Default acceptance requires generated-product MAE at most **0.10** and correct
embedding MSE at most half of the shuffled-target embedding MSE. The alignment
checkpoint and its SHA are the only entry to joint training.

### Phase 4 — `joint`

The accepted alignment checkpoint starts joint training. Its teacher-first
pass proposes latent inputs; its second pass supplies those **generated**
inputs to the same shared causal backbone. Free-running recurrence also trains
through final X/Y error. Teacher X/Y, consumed-product, and next-product
auxiliary objectives remain in joint training to reduce forgetting. Training
retains the original v1 residual HybridLink and growing causal context.

DEV report includes autonomous X/Y, teacher-fed X/Y, next-product metrics,
generated-product metrics, and teacher-conditioned generated-X/Y. It is **not**
an automatic declaration of recurrent success; compare its autonomous per-tier
scores to the original Gaussian and a separately trained static arm. Set
`--arms recurrent,static` consistently across the four commands for that
comparison. The static arm is a no-recurrent-read control, **not** an equal-
FLOP control.

## File layout

```
<output>/config.json
<output>/arms/recurrent/format/latest.pt
<output>/arms/recurrent/format/gate.json
<output>/arms/recurrent/alignment/latest.pt
<output>/arms/recurrent/alignment/gate.json
<output>/arms/recurrent/joint/latest.pt
<output>/arms/recurrent/joint/result.json
```

Each phase has an independent optimizer, RNG and model checkpoint. No
checkpoints are written to the source directory. Existing v1 checkpoints are
not imported, as they lack the new trained next-product head and historical
format-stage model state. This is a fresh, separate v2 curriculum run.

## Provenance and limits

All splits are reconstructed using the original blinded-payoff checkpoint's
identity; matrix-disjoint train/DEV/TEST splits are unchanged. TEST may be
materialized by the source dataset loader but it is **never evaluated** by
this runner. Teacher partial products are derived from six permitted visible
numeric features only. Target X/Y are supervised only, not used as latent
feedback inputs. An exact-zero numerical head is not added by this patch.

This is a numerical analogue of HybridAgent, **not** an upstream reproduction:
its causal backbone begins with fresh sequence-processing weights, it uses four
synthetic partial-product teachers rather than language-token embeddings, and
the pretrained Gaussian MLP is frozen as a seed extractor. Passing phases
certifies the *specified* numerical prerequisites; it does not prove general
symbolic reasoning, publication-level parity, or TEST performance.
