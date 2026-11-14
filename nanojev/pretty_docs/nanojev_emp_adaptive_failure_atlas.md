# NanoJev Adaptive Failure Atlas

## Purpose

Collect enough zero-line arithmetic failures, successful controls, and neural
interventions to determine whether **repeating failure mechanisms** emerge in
independent regions of operand magnitude and signed residual. This is a
**non-hybrid**, read-only diagnostic. The Hard EMP/regular four-head training,
accuracy gates, detector, model weights, and optimizers are never changed.

Use the already trained, memorized first-failure experiment as the source
checkpoint. The atlas does **not** train or pretend that full census memorization
occurred. To compare multiple points along a trained spiral, run separate atlases
against the corresponding archived `probe_snapshot.pt` checkpoints.

## Install

```powershell
python .\new_patch.py "C:\Users\subsi\Downloads\nanojev_emp_adaptive_failure_atlas_patch.zip"
```

The patch creates one tool, one test file, and this document. No existing
NanoJev files are modified.

## First 100,000-case atlas: final checkpoint

```powershell
& $py -m tools.nanojev_emp_failure_atlas `
    --run "C:\Users\subsi\NanoJev\runs\nanojev_emp_first_failure_v1" `
    --campaign "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --hard-state-dir hard_emp_frontier_v2 `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_adaptive_failure_atlas_v1" `
    --checkpoint-source latest `
    --device cuda `
    --census-cases 100000 `
    --min-magnitude 1 `
    --max-magnitude 1000000 `
    --max-delta 64 `
    --discovery-centers 24 `
    --validation-centers 12 `
    --m-window 8 `
    --delta-window 8 `
    --controls 8 `
    --ablate-top 8
```

For a larger atlas, begin a **new** experiment directory and specify
`--census-cases 1000000`, `--discovery-centers 80`, and
`--validation-centers 40`. Within one experiment, resume with **identical**
parameters and the same checkpoint. Do not casually change the candidate
population after seeing validation results.

The existing integer operand encoder limits **each input operand** to
`MAX_ABS = 2,000,000`. The atlas guards its sampling limit accordingly.
Extending to 4,000,000 requires a separate encoder/campaign revision.

## Exact historical checkpoint

The original `nanojev_emp_first_failure_v1` run predates per-ring snapshot
capture and cannot be historically replayed. `--checkpoint-source latest`
explicitly maps *current final-checkpoint behavior* at all operand magnitudes.
It does not claim that the original ring had these same activations.

After rerunning the first-failure program with the per-ring snapshot patch,
use `--checkpoint-source event --ring 6` (or another ring with an actual
`probe_snapshot.pt`) and a fresh Atlas output directory. No snapshot => the
command refuses rather than substituting a final checkpoint silently.

## Method

1. **Census.** Generate unique, reproducible cancellation/near-cancellation
   and same-sign addition examples using mixed log/uniform/power-boundary
   magnitude sampling and signed residuals. The choice is independent of
   prediction or assumed failure type. Store every exact and tolerant outcome.
   Separately track actual sign regime, residual, integer operands, model
   prediction, and numerical error. Shards are gzip JSONL, SHA-256 verified.
2. **Diverse failures.** Select up to N failures per split across magnitude
   bands, signed residuals, sign regime, and error direction. No preferred
   channel, digit mechanism, or carry/borrow model is used to pick centers.
3. **Dense neighborhoods.** Sample the full `(+/- m-window, +/- delta-window)`
   neighborhood on the center's original signed and ordered operand branch.
   Compare each failed center to its nearest successful cases. Only neighbors
   within that split's *same log2 magnitude band* may be used as controls;
   there is no discovery/validation crossover.
4. **Neural evidence.** Capture layer/normalization activation vectors, rank
   normalized anomalous channels, and produce arithmetic and activation
   heatmaps. Store an activation subset for each center and matched controls.
5. **Interventions.** Zero the leading abnormal channels independently,
   measuring whether the center becomes correct *and* how many previously
   correct controls become wrong. These are local interventions, not proof of
   a uniquely responsible neuron. No interventions modify trained weights.
6. **Cross-event validation.** Find recurring features in discovery events.
   Independently test recurrence and repairs in validation events drawn from
   disjoint log2 magnitude bands. A small linear activation classifier is
   trained exclusively on discovery selected-case activations, with
   standardization fixed from discovery. It is tested on held-out bands with
   no hyperparameter tuning using validation labels.

`pattern_emerged` is a deliberately conservative descriptive flag. It requires
one channel to be among the top 8 at **three discovery events** (and meet
`--min-recurring`), among the top 8 in **two validation events**, and to repair
at least one held-out failure under zero ablation. Even when true, this is
**candidate mechanism evidence**, not unique-circuit identification.

The census distribution is deliberately heterogeneous. The balanced local
classifier metrics are **not** natural deployment accuracy or calibrated
hard-case prevalence.

## Output files

- `config.json`: exact source snapshot SHA-256 and immutable sampling settings
- `census/chunk_XXXXX.jsonl.gz`: complete scored census, successful controls
  retained; `chunk_XXXXX.meta.json` contains verified hash and sample count
- `census/census_summary.json`: global/stratified failure rates
- `census_heatmap.svg`: magnitude-band versus operand-sign failure rate
- `events/discovery_XXXX/` and `events/validation_XXXX/`:
  `event.json`, `arithmetic_heatmap.svg`, `activation_heatmap.svg`,
  `local_predictions.jsonl.gz`, `selected_activations.pt`
- `cross_event_activation_heatmap.svg`: recurrent channel matrix, with
  intervention repairs marked separately
- `result.json`: occurrence counts, independent validation, ablation outcomes,
  exploratory predictive metrics and limitations

This output is written to a separate directory. Atomic shard checkpoints make
crash/restart safe. The configuration and source checkpoint are locked to
prevent accidentally mixing changing model states. Incomplete neighborhoods
are recomputed; reports are written only at completion.

## Interpretive limits

The Atlas cannot infer neural causation from a normalized activation anomaly
alone. It cannot reproduce historical activations without saved historical
weights. Top-k events and matched controls are selected by outcome and are
not a random representative deployment population. Only direct intervention
and independent transfer can strengthen a mechanistic hypothesis.
