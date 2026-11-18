# G2-0053 — Repaired Graph Learning

## Purpose

G2-0052 established that a label-blind G1-to-G2 expansion makes 31/32 previously examined counterfactual probe graphs and 15/16 matched pairs reconstructible. That is **candidate coverage**, not learned graph accuracy. This experimental continuation builds a scorer and bounded selector for those candidates without changing G1, Qwen, or the immutable G2-0048 occurrence-evidence parent.

## Execution

From `C:\Users\subsi\main_computer\nanojev`, after applying the patch at the repository root:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-repaired-graph-learning --device cuda
```

Default report directory: `runtime/reports/gamer-v1/graph-g2-repaired-graph-learning-0053`.
The output path is immutable and must not already exist. It contains:

- `g2_repaired_graph_learning_report.json`: coverage, training, candidate selection, historical comparison, heldout metrics, and gating
- `g2_repaired_graph_learning_per_case.jsonl.gz`: audit for original TRAIN and probe cases and the additional fresh synthetic holdout
- `g2_0053_scoring_development.safetensors`: research-only learned edge head, with a SHA-256 recorded in the report
- `g2_0053_frozen_policy.json`: disabled production policy, with a SHA-256 recorded in the report

## Contract and provenance

- G2-0052 is verified by report schema/status, the original predeclared coverage target and the per-case ledger SHA-256. Its immutable ancestor G2-0051 is separately verified by its parent runner.
- The G2-0050 historical candidate/scorer baseline is authenticated through the existing parent chain and reproduced on the exact G2-0051 cohort. The **historical G2-0050 replay-BCE scorer** is used for comparison on those same 32 previously inspected probes. Do not present it as an evaluation of the unmodified G2-0048 head on expanded candidates: the latter lacks G2-0033 summary/prior scores for new edges.
- The actual learned G2-0048 *contextual occurrence encoder* is loaded from its immutable weight file and checked against its SHA-256. It remains frozen. Token windows for new G1 alternatives are computed from the same frozen Qwen hidden-state evidence used by G2-0052. The G2-0053 edge head is the only trainable network.
- Candidate edges and embeddings are created with no graph labels. Author labels enter only afterward for TRAIN supervision and evaluation. The 40 author TRAIN pairs (80 graphs) are used for fitting; eight entire TRAIN pairs (16 graphs) are kept for a separate TRAIN-fit audit; 16 original pairs (32 graphs) form the previously examined diagnostic probe; 16 more deterministic author pairs (32 graphs) form a new probe that did **not** occur in G2-0050–0052. All pair identities stay intact across splits.
- The fresh probe is a newly generated, independent **synthetic TRAIN-domain evaluation**, not a sealed TEST, proof of out-of-domain generalization, or a production promotion gate. The model never uses probe or fresh probe labels to train or select parameters.

## Learner and bounds

The new G2 head scores each directed, type-compatible relationship from the G2-0052 candidate inventory using frozen 96-dimensional contextual endpoint representations, ordered source/destination, learned relation and type embeddings, G1 neural margins, and relative span geometry. It trains with balanced positive/negative logistic loss and hard-negative ranking loss using gold labels from TRAIN only; hard negatives are re-mined against the *full* TRAIN candidate inventory. Defaults: 240 AdamW steps, learning rate `0.0002`, batch size four, 192 negative candidates per graph. The model scores candidate edges in fixed chunks of 2,048; selects at most 512 per graph for ranking, and decodes at most 96 edges with a predeclared zero-logit threshold. It does not consult gold counts at inference.

## Acceptance reporting

The report separately measures (1) original graph representability, (2) recall after the 512-edge ranker cut, and (3) exact decoded graph quality, with missing/extra edges and pair metrics. It compares exact graph accuracy and error counts with the historical G2-0050 matched cohort. A strong result requires preservation of at least 31/32 reachable original probe graphs at the ranking stage and 15/16 complete pairs, at least four additional exact historical probe graphs with lower aggregate errors, **and** independent fresh synthetic holdout thresholds (31/32 rank coverage, 15/16 complete ranking pairs, 12/32 exact graphs, five exact pairs). These gates are research diagnostics; no DEV/TEST or production promotion is authorized even if they pass.

Any failure to reproduce G2-0052's original coverage or hashes aborts the run. No new Qwen or G1 optimizer steps are performed. A negative result points to neural discrimination, cutoff recall, calibration, or decoding, rather than automatically resuming candidate expansion.

## Local verification boundary

Static compilation, actual CLI registration, current parent52 schema/hash verification with the user-provided report, live small-head G1-to-0052-to-0053 neural feature construction, frozen-parent immutability, gradient steps, full fixed 48/16/16-pair scoring and split metrics, real completion callback execution and persisted artifacts, and adjacent regressions can run without CUDA. The **full frozen Qwen CUDA experiment and its measured graph accuracy have not been run locally**. Do not describe these pre-CUDA checks as an accepted CUDA result.
