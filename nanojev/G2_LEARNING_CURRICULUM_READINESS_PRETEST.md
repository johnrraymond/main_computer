# GAMER G2 — Read-only learning curriculum pretest

## Objective

Assess whether existing G2-0048 evidence is sufficient to justify a staged
learning pipeline, **without** running Qwen, retraining any model, selecting
hyperparameters on development data, or changing production checkpoints.

The G2-0048 run achieved 110/111 exact training graphs, but only 9/32 exact
transfer graphs (9/25 candidate-reachable). Its 9/15 allocation versus 0/17
encounter transfer split shows a serious distribution-dependent failure. This
pretest checks the stored per-case evidence for its causes before another
training campaign. **It does not test whether curriculum training improves
performance.** That requires matched candidate-level training features and
predeclared training interventions in a later controlled run.

## Pretest checks

1. Read G2-0048's existing immutable report, ledger, policy, and weight files;
   verify SHA-256 checksums, status, train-only protocol, and no-promotion flags.
2. Match every per-case row to the original frozen TRAIN cohort, refuse missing,
   duplicate, or unexpected prompts; verify arithmetic invariants and reported
   aggregate scores.
3. When G2-0047 is available, validate its corresponding hashes, lineage and
   cohorts, then compare the **same prompts**: corrected, regressed, and net
   exact-graph outcomes. Report an exact paired McNemar/binomial test; report
   its p-value descriptively, not as a release gate.
4. Measure candidate-reachability ceilings, edge ranking inversions, graph
   cardinality errors, and graph exactness by problem family, gold-graph size,
   and family × graph-size intersections.
5. Count *observed* competitive errors: a missed gold edge and an extra edge
   with the same source and relation. This is only a **lower bound**: saved
   error lists are capped at 12 per graph and do not contain all candidate
   labels or scores. Do not interpret zero observable pairs as no ranking
   opportunities.
6. Report which learning methods **cannot** yet be justified using saved
   diagnostic ledgers alone: full candidate-level contrastive supervision,
   vetted paraphrase/role-reversal pairs, and differentiable whole-graph
   training. In particular, never invent a reversed-edge negative label;
   reversed relations can be valid depending on their semantics.

The script never reads DEV or sealed TEST. All cohorts come from the original
G2-0048 TRAIN-only audit and fixed TRAIN transfer partition. It never promotes
or modifies any model, policy, checkpoint, or existing result. By default it
only prints to stdout. Optional `--output` creates a new, explicit report file
and refuses overwrites.

## Apply

ZIP layout: `nanojev/...` relative to repository root; changed files only,
no manifest, no rollback archive.

## Run (from `main_computer\nanojev`)

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_curriculum_pretest
```

For a complete machine-readable report, choose a new filename:

```powershell
& $py -u -m tools.nanojev_gamer_v1_g2_curriculum_pretest --output ../runtime/reports/gamer-v1/g2-curriculum-pretest.json
```

The command requires *no GPU* and will not recapture Qwen embeddings.

### Interpretation

- If unreachable examples dominate a cohort, trainable candidate recovery is a
  prerequisite; no reranking objective can recover absent candidates.
- If reachable failures have many within-group inversions, candidate-level
  ranking data is worth capturing for a contrastive-learning pretest.
- If cardinality errors dominate despite adequate ranking, investigate
  decoder/count calibration before introducing another loss.
- If family gaps remain when controlling for graph size and reachability,
  build **source-valid** counterfactual equivalences and role swaps from TRAIN
  language/graph examples; their labels must be checked and their evaluation
  templates held disjoint.
- If G2-0048 fixes some G2-0047 graphs but breaks others, a replay/retention
  control is justified. Both net and regression counts must be considered.

### Limitations

A data audit cannot demonstrate that a new learning method works, nor prove
that the model has learned causal relationships. Prompt hashes and edge-error
lists do not encode counterfactual equivalence, all positive/negative
candidate pairs, or decoder differentiability. The next training experiment
should be selected only after examining this pretest and the actual candidate
feature/label tensors. Keep the existing G2-0048 checkpoint immutable.

## Testing

```powershell
& $py -m pytest -q tests/test_nanojev_gamer_v1_g2_curriculum_pretest.py
```

Synthetic fixture tests cover ledger authenticity, G2-0047 lineage, cohort
identity, split safeguards, matched regression accounting, competitive-pair
logic, exact paired-test calculation, and no-output-mutation by default.
