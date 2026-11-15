# GAMER G2 — Restore occurrence evidence to the binding-selection path

## Source-level bottleneck (confirmed)

The G2-0031 occurrence pipeline already captures frozen Qwen token windows,
predicted G1 span occurrences, and contextual source/destination information.
However, `nanojev_gamer_v1_g2_structured_graph_decoder.set_features`
(G2-0033) produces **15 numerical edge features** that summarize reference
and alternate scores, rank, parent selection, group density, and graph size.
It does **not** pass the source or destination occurrence vectors, their
original token windows, or the relation identity into the 48-dimensional
G2-0033 edge representation. G2-0045, G2-0046, and G2-0047 operate on this
score-only interface. Training its internal embedding can only transform
those 15 quantities, not recover evidence absent from them.

This is a source-confirmed **information-path restriction**. It is not proof
that this restriction accounts for all graph errors: graph candidate omissions,
encoder insufficiency, and frozen count/decode decisions remain additional
possible causes. G2-0031 previously explored token-aware scoring upstream; this
correction concerns the *later G2-0033 → G2-0047 graph-selection handoff*.

## Correction

The private `OccurrenceEvidenceScorer` now receives, for **each unchanged
candidate edge**:

- Frozen Qwen 5-token windows of the **predicted** source and destination
  occurrences, with validity mask, shared trainable contextual encoder.
- Explicit directed source and destination indices, the binding relation ID,
  predicted occurrence kinds, and span-relative geometry.
- The frozen G2-0033 edge representation and exact parent score as a stable
  starting point.

A directed pair scorer learns how the source/target evidence and the relation
interact. The correction starts *exactly* at the frozen parent score (zero
initial residual) but gradients reach the context encoder after learning starts.
All candidate identities must match the predicted G1 anchor inventory; missing
or mismatched evidence causes refusal, never a silent fallback to score-only
features. No authored graph, author-selected spans, gold edge or prompt string
enters the inference representation.

The G2-0040 candidate inventory, G2-0033 count heads, exact decoder, Qwen/G1
models, and all prior weights stay **unchanged**. This is an isolated repair
candidate, not a production routing change. The original Crossover G2-0047
result remains immutable.

## Controlled acceptance

The corrected head trains only on G2-0046's same predeclared TRAIN-learn
prompts and is evaluated on exactly its original 32 held-out TRAIN prompts.
TRAIN-fit and natural TRAIN-audit cases remain measurement-only. G2-0047's
`frozen:bce` output supplies the comparison baseline (3/32 exact transfer
graphs, 3/25 reachable); its original report, hashes, and per-case ledger are
verified before the experiment runs.

Predeclared evidence threshold: improve **by at least four exact reachable
transfer graphs** while not increasing the sum of missing and extra edges on
reachable cases. This is a practical engineering signal, not statistical
certification. No model is selected on DEV. Sealed/challenge/test remain closed.
There is no automatic promotion even if this threshold is met.

If transfer does not improve, do not interpret that as proof that Qwen lacks
relational information. The remaining possibilities include a different
representation architecture, insufficient TRAIN supervision, candidate
coverage, and the fixed decoder/cardinality constraints.

## Apply and run

Patch ZIP: changed files only, root-relative paths (`nanojev/...`), no
manifest, no rollback ZIP. Apply from the `main_computer` repository root.

From `main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-occurrence-evidence-repair --device cuda
```

Output directory:

`runtime/reports/gamer-v1/graph-g2-occurrence-evidence-repair-0048/`

Files: `g2_occurrence_evidence_repair_report.json`,
`g2_occurrence_evidence_repair_per_case.jsonl.gz`,
`g2_occurrence_evidence_repair_development.safetensors`,
`g2_occurrence_evidence_repair_frozen_policies.json`.

`development_evaluation_skipped=true`, `sealed_test_used=false`,
`production_promotion=false`, `g2_certified=false`, policy `enabled=false`.

The diagnostic-only repair and its tests can be verified on CPU without the
entire Qwen model. Full model effectiveness is unknown until the existing CUDA
capture/evaluation pipeline executes.
