# NanoJev — large-population hard-case discovery

## Objective

Discover **what distinguishes hard zero-line examples** from easy examples for the
frozen **non-hybrid** four-bank EMP backbones. No carry/borrow mechanism is
preselected. The existing matched-borrow intervention is **not run**.

A hard case is primarily a cancellation example that **all four** banks fail under
the current diagnostic accuracy rule (exact for residual 0, within 5% otherwise).
The full spectrum of zero, one, two, three and four wrong banks is retained.
No new model is trained or introduced, and the four-bank campaign is not modified.

## One command

Stop the four-bank trainer first. From the Main Computer repository:

```powershell
& $py -m tools.nanojev_emp_hard_discovery `
    --output "C:\Users\subsi\NanoJev\runs\nanojev_emp_dual_bank_outward_v1" `
    --ladder-output "C:\Users\subsi\NanoJev\runs\nanojev_zero_line_ladder_v1" `
    --radius 16384 `
    --device cuda `
    --target-hard 20000 `
    --max-candidates 500000
```

This aims for **20,000 examples failed by all four banks** and scans no more than
**500,000 distinct operand pairs**. It stops on whichever threshold comes first;
the final report explicitly says whether the hard-case target was reached.
Increase `--max-candidates` (before the first run) if more examples are needed.
The default chunk size is 4,096 cases, with incremental progress and committed
compressed chunks after each evaluation. If interrupted, rerun the **identical**
command with `--resume`; the program verifies the manifest and SHA hashes and
reconstructs the sampler deterministically, without re-evaluating completed
chunks. Do not resume against a changed checkpoint.

`--radius` may be omitted to diagnose the latest saved stage. This is not a
training command: no `optimizer.step()`, no new gate, no checkpoint mutation,
no HybridAgent, and no specialized arithmetic router.

## What is sampled

Two interleaved, source-labeled, deterministic proposal populations:

- **Native:** the current outward TRAIN generator, including its frontier and
  replay distribution.
- **Broad:** a separate, controlled mixture of zero, unit, small, logarithmic,
  uniformly drawn, and numerical-boundary residuals, with diverse operand bands,
  magnitudes, signs and orientation.

Neither proposal inspects a model's answer before deciding what to sample. Exact
operand pairs are globally deduplicated, and only TRAIN-fold anchor families in
the existing `mine` partition are evaluated. Specialist-reserved families,
DEV, and sealed TEST are not touched. All cases satisfy `a + b = residual` and
the stage's legal operand and residual bounds.

The mixture is an experimental population, not an unbiased distribution of all
integer addition. Failure rates apply **to this explicit mixture**. Source-labeled
cohorts are reported separately to detect when conclusions depend on the sampler.

## What is retained

Outputs default to a new sibling folder such as
`nanojev_emp_dual_bank_outward_v1_hard_discovery_16384/`, **outside** the
campaign. No existing campaign data is overwritten.

- `manifest.json`: seed, stage, checkpoint hash, candidate settings, chunk
  hashes and resumable progress.
- `chunks/chunk-*.jsonl.gz`: **every scored example**, including successes and
  intermediate failures. Each record has both operands, true residual,
  checkpoint hash, sampler, original anchor, four predictions and four-bank
  correctness labels. These are the necessary control population.
- `all_four_wrong.jsonl.gz`: **all discovered four-bank failures**, without a
  512-case export limit.
- `report.json`: total failure counts, bank-specific failure rates, full-population
  structural cohorts, ranked input features discovered on training-diagnostic
  anchor families and their separately measured rates on heldout diagnostic
  anchor families, plus a simple interpretable detector and representative
  hard cases.

The analysis uses the *whole population* for feature enrichment and a uniformly
sampled mixed-success reservoir for the decision tree. Detector features are
input-only. No target, predictions, error, or difficulty label is provided as a
predictive input. Borrow/carry descriptors, if discovered, are **candidate
correlates among many others**, not the objective or a privileged treatment.

## Scientific interpretation

The target is **predictive, reproducible structure in failures**. A feature
identified on one set of TRAIN anchor families must still distinguish failures
on disjoint diagnostic anchor families. Negative results are reported as such.
The reserved split makes anchor families unseen *to this detector*, not
necessarily unseen to the four already-trained backbones. A strong detector
suggests *where* the backbone is weak; it is not proof that the feature causes
the failure. A later causal intervention can be designed around whatever
patterns actually emerge, instead of presupposing borrow as the explanation.
