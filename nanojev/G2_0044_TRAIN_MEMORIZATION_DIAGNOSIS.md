# G2-0044 — TRAIN memorization and learning-machinery diagnosis

## Governing question

G2-0043 did not show whether any of the three trained heads could recover **the
same reachable TRAIN graphs on which they were optimized**. Its zero new exact
graphs on disjoint TRAIN-fit therefore cannot distinguish failed optimization,
inadequate head/feature expressivity, interference between training graphs, and
generalization failure. G2-0044 tests these possibilities without proposing
another production model.

## Parent contract

The experiment verifies the exact immutable G2-0040, G2-0041, G2-0042, and
G2-0043 lineage, including SHA256 digests, frozen policies, model tensor shapes,
720 ordered G2-0043 TRAIN-fit ledger rows, and the already-declared no-winner
states. It reuses the G2-0040 capture with G1 and parent G2 weights frozen.
A lineage or tensor mismatch refuses the experiment.

## Cohort and controls

Use the G2-0040 enriched TRAIN collection. The fixed selection rule sorts the
TRAIN-only `repairable` graphs by SHA256 of the prompt and takes the first six;
the first one is the single-graph probe. The next six disjoint reachable TRAIN
graphs provide an unseen TRAIN diagnostic. The complete previous TRAIN-fit 80
and independent natural TRAIN-audit 80 can be **measured only**, never used to
train, choose examples, or select a winner. DEV and sealed TEST are never read.

Before the optimization runs, an oracle assigns +24 to correct shortlisted
relationships and -24 to other shortlisted relationships. With unchanged group
count and graph count model outputs, the real G2-0033 decoder **must** reconstruct
every probed graph; otherwise G2-0044 refuses with a decoder alignment error.
This is a TEST-ONLY diagnostic oracle and cannot be deployed.

For the first TRAIN graph, a second control trains **independent** per-edge
logits with plain binary cross-entropy, maintaining frozen count predictions
and the exact decoder. It establishes whether direct score optimization can
reach an exact graph in the fixed step budget. Failure is a bounded optimization
finding, not proof of impossibility.

## Overfit experiments

For each G2-0043 learning arm—`absolute`, `group_margin`, and `joint_count`—
start from the **same frozen G2-0033 parent weights**, train the authorized
readouts on the *same* one TRAIN case, then restart from the parent and train on
all six. No training examples are randomly dropped. Reuse the same original
G2-0043 objective and frozen count configuration so a failure cannot be blamed
on substituting a new architecture. Gradients, parameter-update norms, gradient norms, loss components,
and exact-graph checkpoints are recorded. The frozen encoder is also audited
for identical edge-readout representations carrying contradictory TRAIN labels.
Such an exact collision establishes an expressivity limit on the frozen edge
readout; its absence does not establish adequate capacity. The default diagnostic budgets are
480 single-case and 840 six-case optimization steps, with periodic exact-decode
evaluation. Both can be overridden, up to 3000 steps, in a **new immutable run**.

At each endpoint, evaluate the same model on its one-graph TRAIN set,
six-graph TRAIN set, the six disjoint reachable TRAIN graphs, the 80 TRAIN-fit
examples and 80 naturally distributed TRAIN-audit examples. None of the latter
three sets may feed model optimization. Every case reports the exact graph,
missing and false relationships, edge-ranking inversions, and the worst
within-group positive/negative score separation. Output weights are diagnostic,
not production candidates.

## Decision interpretation

* **Gold-score oracle fails:** decoder/candidate/representation alignment
  mismatch (experiment refuses). Fix this before any learning claims.
* **Direct-score optimization fails:** simple differentiable score optimization
  did not achieve complete graphs in its fixed budget; inspect loss and the
  decoder's selection thresholds. The synthetic oracle still proves reachability.
* **Direct scores succeed, but no head memorizes one graph:** model-head
  optimization/feature/gradient/objective issue is still unresolved. Fix the
  learning machinery before broadening the architecture search.
* **One graph fits, six do not:** cross-example interference, optimization
  capacity or feature collisions need investigation.
* **Six fit, but disjoint TRAIN does not:** genuine memorization is demonstrated;
  the next task is generalization and training coverage, not basic learnability.
* **Disjoint TRAIN also works:** proceed to a separately predeclared candidate
  training/evaluation gate; this experiment has **no promotion route**.

A missed step budget is reported as `not demonstrated`, not `cannot learn`.
No DEV/model-policy/threshold selection, seal opening, or production mutation
is permitted.

## Invocation

From the `nanojev` working directory, after applying the repository-root-
relative patch from the Main Computer repository root:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-train-memorization-diagnosis --device cuda
```

To run a longer **new** bounded probe, optionally specify `--single-steps 900 --multi-steps 1500` and a distinct
`--output` path. Re-running against the same
output directory is refused, so all results remain immutable.

Default output:
`runtime/reports/gamer-v1/graph-g2-train-memorization-diagnosis-0044/`

Outputs: `g2_memorization_diagnosis_report.json`,
`g2_memorization_diagnosis_per_case.jsonl.gz`,
`g2_memorization_diagnosis_development.safetensors`, and
`g2_memorization_diagnosis_frozen_policies.json` (always disabled).
