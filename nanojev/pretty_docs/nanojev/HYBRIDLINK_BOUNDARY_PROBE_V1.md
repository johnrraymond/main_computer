# HybridLink v1 — read-only format / alignment / recurrence diagnostic

This independent diagnostic examines **saved checkpoints** from
`tools.nanojev_hawk_dove_hybridlink_sequence`. It never trains, updates, or
resaves the reference Gaussian model or HybridLink states.

## Install and run

Use `python new_patch.py nanojev_hybridlink_boundary_probe_patch.zip --dry-run`
followed by the same command without `--dry-run`.

Run from the Main Computer repository, using the same Python interpreter that
was used for the original experiment:

```text
python -m tools.nanojev_hawk_dove_hybridlink_boundary_probe --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" --run-dir "C:\Users\subsi\NanoJev\runs\hawk_dove_hybridlink_sequence_v1" --device cuda
```

The output is a new
`C:\Users\subsi\NanoJev\runs\hawk_dove_hybridlink_sequence_v1\boundary_probe_v1\report.json`.
For a fresh report at the same location, explicitly pass `--overwrite-report`;
otherwise an existing report causes a refusal. You can also pass `--output`
to select a different, initially empty report directory.

By default it inspects `recurrent/best.pt`, `recurrent/latest.pt`,
`static/best.pt`, and `static/latest.pt`. Choose a subset with
`--arms recurrent` or `--checkpoints best` if necessary.

## Questions the report answers

| Diagnostic path | What it asks |
| --- | --- |
| `teacher_tokens` | Can the saved backbone/head solve X/Y when given the **correct** four intermediate product embeddings? |
| `teacher_conditioned_generated` | Can the backbone/head solve X/Y from HybridLink embeddings generated with teacher-forced prefix states? |
| `autonomous_generated` | How accurately does the full recurrent or static model solve X/Y using **only self-generated thoughts**? |
| `zero_thoughts`, `repeat_first` | Does corrupting the generated-thought sequence change held-out outcomes? These are out-of-distribution ablations, not equivalent-trained controls. |
| `format_probe.product_after_consuming_teacher` | Does the trained value probe recover a product from the position **after it has seen that product token**? |
| `format_probe.next_product_from_preceding_state` | Does the same probe output the *next* product before the token arrives? **Caution:** the value probe was not trained at these earlier positions; its result cannot certify next-token prediction competency by itself. |
| `alignment_probe.per_step.teacher_conditioned` | For each of four positions, how near is HybridLink's teacher-conditioned output to the corresponding teacher input embedding? |
| `alignment_probe.per_step.autonomous` | How near are freely generated recurrent thoughts to those teacher embeddings? |
| `paired_teacher_vs_other_paths` | Within each tier, how many all-mask instances are rescued or regressed relative to correct teacher-fed performance? |

The generated and free-running branches never receive oracle X/Y or oracle
products as inputs. Teacher product embeddings are intentionally supplied only
to **diagnostic teacher-conditioned branches**.

All comparisons use the same DEV cases, both presentation masks, and the
original strict per-X/Y relative 5% criterion (unless `--tolerance` is changed).
The TEST split is **never scored**. The report includes input SHA-256 values,
checkpoint identity checks, and a post-run unchanged-file check.

## Limitation: prior stage boundaries were not checkpointed

The original v1 trainer saved `best.pt` only after joint-stage probes and
`latest.pt` as a rolling state. Following a completed run, these are both
**joint-training** model states. The recorded training history contains
stage names and **free-running** scores, not stage-specific model weights.

Therefore, this probe diagnoses what the current saved states can do under
format-, alignment-, and rollout-style **evaluations**. It cannot reconstruct
what the model knew **at the end of format activation** or **at the end of
alignment**. The report states this explicitly in `checkpoint` and
`provenance.limitation`. Historical stage-local measurement would require
previously saved stage-boundary checkpoints; do not infer them from the
final weights.

## How to interpret outcomes

- **Teacher token scores already poor**: the answer pathway does not reliably
  compute X/Y even when supplied ideal intermediate products. Investigate the
  format/answer learning objective before blaming HybridLink.
- **Teacher strong, teacher-conditioned generated weak**: look at per-step
  alignment errors, norm ratios and cosine scores. The link may not produce
  consumable teacher-compatible embeddings.
- **Teacher-conditioned strong, autonomous weak**: teacher-forced state
  distribution and autonomous recurrence diverge. Compare autonomous vs
  teacher-conditioned embedding errors at steps 1–4.
- **All three strong but recurrent ≤ static**: extra recurrence has not shown
  a benefit over the matched static computation.

These are **diagnostic interpretations**, not automatic claims about which
historical training stage failed. No acceptance gate is weakened.
