# G2-0084 — Post-Hoc Binding Failure Attribution

The G2-0083 `resume` result showed 0 exact graphs on both historical and prospective splits, with prospective edge F1 near 24%. This is *not* a G2 certificate.

## How to run

At the `main_computer/nanojev` directory:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_binding_forensics_0084
```

The command only reads G2-0083's **already scored** seal, blind predictions, G2 checkpoint, and prospective report. It verifies SHA-256 commitments, recomputes previously reported edge metrics and G1 reachability, then writes a separate new diagnostic report to `../runtime/reports/gamer-v1/oracle-fabric-g2-binding-forensics-0084`.

The output files are `report.json` (aggregate, per family, per binding type and each trained/untrained/ablated arm) and `cases.jsonl` (case-level attribution). It has no training operation, new model selection, seal mutation, or G2/G3 stage gate bypass. The output path is immutable; rerun only with a new `--output` path.

## Attribution

Each gold binding is either **unreachable** because one or both G1 endpoints were omitted, or **reachable** because both were predicted. A reachable gold edge absent from the trained edge set is a G2 omission. A predicted edge outside gold is an extra edge; the report distinguishes a **wrong label for an actual gold endpoint pair** from an edge between endpoints that should not be linked at all.

The report also records the count of cases with exact G1 object reconstruction but incorrect G2 bindings, binding-type precision/recall/F1, per-family rates, the learned-vs-untrained edge changes, and cases with no decoded binding edges. Together these reveal whether the first intervention should address endpoint completeness, learned pair discrimination, decoding, or supervision/negative sampling.

A lower sampled-pair training loss does not imply complete graph reconstruction. The current runtime's all-directed-pair argmax/confidence decoding is not equivalent to its training-time sparse negative sampling. This diagnostic makes that hypothesis testable but does not presume it is the sole cause.

## Integrity and interpretation

All original G2-0083 files are read-only. The module checks the committed answer/prediction hashes, checks trained checkpoint hash, validates case/gold correspondence, checks all predicted edges are anchored in their declared G1 objects, and checks its recomputed aggregate metrics against the G2-0083 scored prospective report. It rejects discrepancies rather than silently reporting them.

**Caution:** This is post-hoc analysis of *already scored* examples, not a second prospective experiment. Any subsequent tuned decoder or retrained model must use new training/selection material and a new unexposed seal for acceptance. All original G2 development/causal gates and G3 lock remain in force.
