# G2-0058 — Object Binding Diagnosis and Targeted Repair

## Objective

Determine whether the G2-0057 set predictor loses correct relationships through
(1) source/destination neural discrimination, (2) candidate-constrained decoding,
(3) null/slot assignment, or (4) graph cardinality. Where validation gives a
trainable endpoint-binding signal, compare the **same** G2-0057 combined paired
supervision with and without a candidate-aware, same-label hard-negative loss.

No architectural change, no G1 or G2-0052 mutation, no training of the frozen
context encoder, no sealed-test access, and no production promotion.

## Read-only diagnostic — before optimization

Load and hash-check the actual G2-0057 selected `D_combined` step-720 checkpoint,
policy, report, candidate ledger, and prediction ledger. Reuse the exact 80
training graphs and 16 validation graphs, with the old probe/final cohorts
checked for overlap. Baseline validation inference receives no author labels.

Only **after** the native prediction, use gold edges for diagnostic analysis:

- Hungarian match true triples to prediction slots, expressly an **oracle
  assignment**. Record each matched slot's gold source, destination, relation,
  and whole admissible-edge ranks, with same-label wrong-source and
  wrong-destination score margins.
- Compute clearly labeled nondeliverable oracle interventions: gold label,
  gold source, gold destination, both endpoints, matched active/null slots,
  and the full gold triple. Report exact graphs and false positives/negatives
  under each intervention. Full oracle recovery is a representability ceiling,
  **not** the model's achievement.
- Check that the local decoder reproduces G2-0055's native decoding exactly.
  A disagreement aborts rather than fabricates a decoder diagnosis.
- Do not train if validation provides no endpoint misranking and no same-label
  admissible competing endpoints. The failure is printed as
  `GAMER_G2_0058_NO_TRAINABLE_ENDPOINT_BINDING_SIGNAL`.

## Matched causal comparison

Both arms begin with identical frozen G2-0057 selected weights and identical
sample/pair order, 40 training pairs (80 graphs), 960 steps per arm,
16 validation graphs, identical AdamW settings and validation checkpoint rules.

- `A_continue`: exactly the prior G2-0057 `D_combined` supervised set loss plus
  its training-only canonical-ID aligned paired consistency.
- `B_hard_binding`: the same objective, plus 0.25 × hard-negative loss. Each
  matched gold slot competes against at most 8 highest-scoring **admissible
  same-relationship alternatives** that keep one endpoint fixed and replace
  the other. The per-slot penalty is `softplus(0.5 + negative - gold)`.
  Mining uses training gold only, not candidate-generation or inference gold.

Checkpoint/arm selection remains validation-only: most exact graphs, then
fewest edge errors, then fewer misses, exact pairs, earliest step, with control
favored on a full tie. Save both arms' best checkpoints.

## Final evaluation

Generate one *new* 16-pair (32-graph) final cohort by extending the same
prefix-stable counterfactual author; verify all 0056/0057 prompt identities
and the new cohort's disjointness. After both checkpoints and validation
selection are frozen, score **both** arms once on that new cohort and report
exact graphs/pairs, error counts, edge recall/precision, graph count error,
family and counterfactual mode. Also report the three previously evaluated
probe/final cohorts as historical context. Do not use these evaluations for
checkpoint selection. The new final cohort is train-domain synthetic data;
this is **not** a sealed development-to-production test.

## Execution

From `nanojev` after applying the repository-root-relative ZIP at the
`main_computer` repository root:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0058-binding-repair --device cuda
```

Immutable output:

`runtime/reports/gamer-v1/graph-g2-0058-binding-repair`

The output contains a report, candidate ledger, post-hoc diagnosis and model
prediction ledger, frozen selected control/repair weights, and a disabled
research-only policy. Parent/checkpoint hash failures and decoder disagreements
abort before promoting or modifying any parent artifacts.

## Decision

Treat endpoint accuracy as improved only if the intervention produces a
measurable improvement against the frozen control in validation and/or the
predeclared new test. Separate improvement in exact graphs from improvements
in edge-level scores. No production promotion is authorized by this experiment.
