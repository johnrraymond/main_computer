# HybridLink format-phase diagnostic

This read-only tool investigates a FORMAT gate plateau without changing the
existing curriculum, optimizer checkpoint, Gaussian seed, DEV gates, or TEST.
It uses the existing experiment `config.json`, refuses identity drift, and
loads the current `arms/recurrent/format/latest.pt` checkpoint.

```powershell
& $py -m tools.nanojev_hawk_dove_hybridlink_format_diagnostic `
    --reference-checkpoint "C:\Users\subsi\NanoJev\runs\hawk_dove_gaussian_2000_blind_matrix_v1_replay-1\best.pt" `
    --output "C:\Users\subsi\NanoJev\runs\hawk_dove_hybridlink_curriculum_v2" `
    --device cuda `
    --report "C:\Users\subsi\NanoJev\runs\hawk_dove_hybridlink_curriculum_v2\format_diagnostic.json"
```

The concise `CURRICULUM_FORMAT_DIAGNOSTIC` line contains scores for the
normal X/Y head, reconstruction by summing the existing consumed-product
probe, reconstruction by summing the next-product probe, the algebraic exact
product sum, and the response to shuffled teacher tokens. The JSON report
adds per-tier and zero/near-zero instance breakdowns, product MAE, plus
independent gradient norms of the answer and product-training objectives.

All reported numerical reconstructions are **diagnostics**, not replacements
for the accepted inference head. A weak strict 5% score on a product sum can
coexist with low absolute product MAE because the score requires exact output
when the target is zero. A strong reconstructed score with weak direct-head
score implicates the final readout; if both learned product readouts fail,
inspect per-step errors and precision near zero. Oracle summation checks
whether the four visible partial products reconstruct the labels and is not a
learned-model result. Shuffling uses distinct product signatures and no X/Y
oracle to construct donors; a performance change establishes dependence on
the teacher-token stream but does not alone establish arithmetic reasoning.

The checkpoint and original source are SHA-checked before/after inspection.
TEST samples are not passed to an evaluation function. The optional report is
written only when `--report` is provided; do not point it at an input model.
