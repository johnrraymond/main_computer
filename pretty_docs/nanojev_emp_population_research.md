# NanoJev EMP Population — experimental numeric heads

## Objective

Run four genuinely distinct, simultaneously trained sign-and-digit readouts on **one** trainable recurrent AdditionNet backbone. Observe histograms of decoded prediction / true residual and adjust a head's training pressure or learning rate **during the campaign**, without touching the original outward ladder or opening the sealed TEST family.

This is a research experiment, **not** an inference router, arithmetic oracle, production model, lexical language model, or full arithmetic certification. The independently sampled DEV views of the old outward runner are **not** heads; each new learned head is measured against *both* held-out DEV views. The addition model accepts only two integers; the `repeated_digits` head is a **numeric input digit repetition proxy**, not a lexical-repetition (token sequence) implementation. A true lexical-repeat EMP will require a token-capable backbone, corresponding training examples, and independent linguistic DEV cards.

## Files

- `tools/nanojev_emp_population.py` — train/resume a separate EMP population branch.
- `tools/nanojev_emp_tune.py` — atomic operator changes to `controls.json`, with monotonic revision.
- `tests/test_nanojev_emp_population.py` — isolated numeric-feature, gradient, gate, histogram, tuning, and training/resume CPU tests.

## Start from the stopped ±512 research checkpoint

Do not continue writing to the source run while this EMP branch is active, because the source checkpoint SHA is pinned at creation. The existing staged checkpoint at 15,000 steps is a research parent, **not** an accepted ±512 certification.

```powershell
& $py -m tools.nanojev_emp_population `
    --source-stage "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_outward_return_80pct_v1\stages\outward_512" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_population_512_v1" `
    --radius 512 --device cuda --steps 3000 --probe-every 250
```

Resume with exactly the same arguments plus `--resume`. `--steps` may be increased for a longer experiment. This is a *separate experiment* and does not promote the outward ladder radius.

## Live adjustments from a second terminal

Example: assign more loss pressure and larger frontier exposure to the left EMP. Changes take effect at the next training probe boundary (250 steps with defaults); training continues without resetting optimizer states.

```powershell
& $py -m tools.nanojev_emp_tune `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_population_512_v1" `
    --head left --loss-weight 5 --frontier-boost 6 --lr 0.00008
```

Or change only the common backbone learning rate:

```powershell
& $py -m tools.nanojev_emp_tune `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_population_512_v1" `
    --backbone-lr 0.000008
```

`controls.json` may also be edited with a text editor; if edited manually **increment the `revision` integer** before saving and use atomic replacement to avoid partially written JSON. The supplied Python tuning command is safer. `lr`, `loss_weight`, `frontier_boost`, `zero_boost`, and `repeat_boost` are available for each head; `backbone_lr` is global. The tensor shapes and optimizer type cannot be hot-changed. Controls are validated strictly, with nonfinite values refused. When controls change, the new revision and effective step are checkpointed in `control_log`.

Default EMP hypotheses:

| Head | Additional training emphasis | Relative loss weight |
|---|---|---:|
| `left` | Frontier of new residual radius | 3 |
| `right` | Balanced arithmetic | 1 |
| `zero` | Exact zero / small residual | 1 |
| `repeated_digits` | Operands containing adjacent equal decimal digits (e.g., 111) | 1 |

All heads see the same TRAIN-only cards and all receive supervised sign+7-digit cross entropy. Inputs to network inference contain only the two operands. Head emphasis weights use TRAIN labels and input patterns, **never the held-out DEV gold labels to build training batches**. Retention rehearsal uses the original certified arithmetic teacher, through the shared backbone.

## Readout and gate

At each probe, evaluate every EMP against the same two fixed DEV views with two scopes:

- `frontier`: new residual shell.
- `outer`: all nonzero residuals of magnitude at least 33 in a broader held-out DEV deck.

The research binary `qualified_80_50` is true iff left achieves **at least 80% within 5%** on both scopes and both views while right achieves **at least 50%** on both scopes and both views. The `zero` and `repeated_digits` heads are measured, not advancement gates. By default, the runner does **not** stop when this gate first passes; it continues gathering training curves. `--stop-on-gate` changes that behavior.

`histogram.svg` contains a separate comparable numerical histogram for every EMP, using identical fixed bins of signed `q = predicted / true`. The red `q=0` line is the tipping point; the green `q=1` line is the correct result. `history.json` records full histogram bin counts, within-5% accuracy, tipping violation rate, interquantile spread, and conditional zero, inner, outer, and repeated-digit slices by head and DEV view. Results are distributions *over test cases*, not Bayesian distributions for an individual prediction. Changes in a head's histogram can be compared against the same fixed problems over successive probes.

The original outward training lineage, acceptance gates, checkpoint contents, test embargo, and existing monitor code are unchanged.
