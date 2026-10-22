# NanoJev: 2,000-bank prompt-conditioned Gaussian density experiment

This is the *next* research experiment after the 200/2,000/20,000/200,000
**shape-only** bank-count sweep. The earlier sweep learned amplitudes for **one
known synthetic target**. It could not test inference from unseen Hawk–Dove
prompts. This new independent lineage learns a *different X and Y density per
prompt* and tests on unseen source instances.

## Training invariant

The input to each prediction is extracted **only from the visible prompt**.
The teacher's `x`/`y` fields are targets for training and evaluation, never
inference inputs. Every Gaussian contribution has nonnegative mass on the same
256-bin normalized coordinate grid. Two output heads use the same 2,000 fixed
Gaussian centers and widths but learn different conditional mixture weights.
There is **no LHS/RHS gate**, no hard overflow vote, no learned bank-by-bank
classification, and no gradient through held-out DEV or TEST.

The conditional learner uses a small neural network to propose continuous X/Y
locations from structured prompt-visible numeric features (and tier), then
weights the 2,000 Gaussians according to their distances from those locations
plus learned bank gains. The aggregate is the sum of all weighted density
contributions, globally normalized per X/Y field. Most distant contributions
are negligible (the fixed kernels have a five-sigma truncation).

This **does not yet plug into NanoJev's existing frozen encoder**, its 21-bank
checkpoint, or a raw-token language model. It is an isolated structured-numeric
conditional learner, designed to establish learnability of the complete
density-reconstruction path. The payoff table in the dataset remains the fixed
teacher fixture; generalization across payoff matrices is not established.

## Commands (run at the repository root)

Use the Python interpreter that has PyTorch/CUDA and the previous population
density patch installed.

```powershell
# Unit tests (CPU)
& $py -m unittest tests.test_nanojev_hawk_dove_gaussian_population_conditional -v

# Only if runtime/hawk-dove-million/teacher.sqlite3 DOES NOT ALREADY EXIST:
# & $py -m tools.nanojev_hawk_dove_million build `
#    --fixture examples/hawk_dove_million_DEMO_ONLY.json `
#    --out runtime/hawk-dove-million/teacher.sqlite3 `
#    --instances 201 --mutation-mode lattice

# Full 2,000-bank feature-conditioned experiment
& $py -m tools.nanojev_hawk_dove_gaussian_population_conditional train `
    --database runtime/hawk-dove-million/teacher.sqlite3 `
    --banks 2000 `
    --device cuda `
    --steps 15000 `
    --probe-every 500 `
    --batch-size 64 `
    --lr 0.003 `
    --min-lr 0.00003 `
    --view-cases 2 `
    --output "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_population_2000_conditional_v1"
```

To **resume an interrupted run** with the same configuration, add `--resume`.
The run saves `latest.pt` at each DEV checkpoint and `best.pt` whenever DEV
mean total variation improves. It refuses changed settings, a different
database checksum, and accidental overwrites of completed runs. The experiment
starts from new conditional model weights; there is no transfer from the 50-step
shape-only fit or the 21-bank model.

After training, read `summary.json`. This includes TRAIN/DEV/TEST overall and
per-tier X MAE, Y MAE, Z/ARGMAX accuracy, target-to-prediction KL, and
aggregate total variation. `history.json` records the training checkpoints.
The `dev_density_views/` subtree renders the *same selected held-out prompts*
at each probe as CSV and SVG, with target, untrained population baseline, and
learned aggregate on a common axis. T08 and T03 examples take precedence;
T00–T02 are also reported separately because their prompts expose raw X or Y.

For independent label-free inference on a saved text prompt:

```powershell
& $py -m tools.nanojev_hawk_dove_gaussian_population_conditional predict `
    --checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_population_2000_conditional_v1\best.pt" `
    --tier T08 `
    --prompt-file "C:\path\to\one_t08_prompt.txt" `
    --device cuda
```

The `--representation raw_logbar_scientific` option must be supplied to
`predict` when the teacher database used scientific LogBar presentations.
There is no oracle read in `predict`.

## Supervision, split, and metrics

- Existing `nanojev_hawk_dove_million` SQLite teacher is read-only; each
  opponent-count source instance stays wholly in TRAIN, DEV, or TEST even if
  presented in many tier/mask forms.
- T00 visibly contains raw X and Y; T01/T02 visibly contain a raw oracle result
  and the numerical opponent counts; T03–T08 show counts only. Prompt parsing
  strictly follows those contracts. T07's English and Lisp counts must agree.
- A numerical grid range is derived **only from TRAIN labels**, with padding.
  If held-out labels fall outside that range, the run fails closed instead of
  silently clipping them to the boundary.
- Source-instance overlap is rejected. Input extraction never uses the teacher's
  `instance_id`, hidden `a`/`b` columns, or oracle `x`/`y` fields.
- TRAIN loss uses KL plus squared density reconstruction error and a bounded
  numeric location auxiliary loss. AdamW with cosine learning-rate decay and
  gradient clipping replaces the instability-prone constant `0.2` used in the
  shape-only sweep.
- Checkpoints are selected using DEV total variation. TEST is **never** read
  for optimization, gradient updates, checkpoint selection, or hyperparameter
  adjustment inside the harness, and is evaluated once after the best DEV
  checkpoint is restored. Repeated manual sweeps using TEST scores to pick
  configurations would still invalidate TEST as a final blind evaluation.
- The predicted Z is the comparison `X > Y`, `X < Y` or exact tie; no separately
  trained Z head. Near-tie calibration is not established.

## Verification in the patch environment

Four focused CPU tests validated per-tier visible-number extraction (including
T07 prose/Lisp agreement), bank-density normalization and gradients,
TRAIN/DEV/TEST isolation, saved checkpoints, a short genuine optimizer run,
prediction without oracle labels, and config-drift refusal. The five existing
shape-only population tests also pass. A separate actual 2,000-bank CPU smoke
used a newly created demonstration teacher with 41 source instances and 1,500
training steps; DEV TV decreased from 0.893812 to 0.246096, and its held-out
TEST TV was 0.205121. This is a *different small synthetic fixture*, not the
user's existing teacher database. CUDA execution, production teacher results,
and end-to-end NanoJev encoder integration remain unverified.
