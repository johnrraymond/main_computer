# G2-0024 — Context utilization and gradient audit (frozen G2-0023)

## Decision being tested

G2-0023 introduced candidate-specific attention but its relational-context
arm performed essentially identically to its capacity-only arm. This audit
asks whether that result means the saved contextual branch contributes little,
or whether it contributes meaningful but unhelpful or harmful information.
It **does not retrain, tune a threshold, change an architecture, select a
checkpoint or unlock production**.

## Parent and cohort contracts

Load the immutable G2-0023 report, compressed full per-case ledger, and the two
G2-0023 exploratory arms' saved weights. Verify their SHA-256s, exact tensor
keys and shapes, finite tensors, fixed training protocol, lineage back through
G2-0022 and earlier, and all recorded G2-0023 DEV aggregates. Reconstruct the
same **448 new G2-0023 development cases** from seeds `460028` and `460029`
(320 independent + 128 withheld topology), without generating fresh cohorts,
TRAIN batches, or sealed TEST/challenge examples. Replay all 448 parent
`relational_context_0023` graph decisions and competition metrics exactly;
replay `capacity_only_0023` graph metrics as an additional checkpoint guard.
Abort on any mismatch.

## Predeclared frozen interventions

All interventions use **the same saved** `relational_context_0023` model, the
same fixed 0.75 decision threshold, the same G1/compatibility/top-24 candidate
inventory and the same candidate-local features. Only the experimental
residual branch's competitor signal changes:

- `full_context`: unchanged original G2-0023 contextual forward pass.
- `residual_disabled`: original trained scorer path only, removing the added
  contextual residual outright. This is the primary branch-utilization test.
- `peer_suppressed`: retains local candidate transform but zeroes the peer
  context and contrast inputs to the residual block.
- `uniform_attention`: replaces candidate-specific learned attention weights
  with equal weights over the other candidates in the same source/relation.
- `misaligned_peer_values`: rotates learned value vectors within each
  source/relation group while leaving candidate keys and query weights in
  place. This is an intentionally invalid counterfactual for diagnostic use
  only and never an inference configuration.
- `self_only_counterfactual`: the existing G2-0023 self-only calculation,
  applied with the *context-arm weights*. This is not the separately trained
  capacity-only control and is not interpreted as a trained-model comparison.

No cross-group information enters the real full-context scorer. No competitor
intervention reads gold labels. Group singletons contribute exactly zero
contextual residual.

For each case and intervention, report per-candidate absolute logit movement,
threshold-crossing decision flips, full-vs-ablated gold and false-positive
counts, full graph micro F1/precision/recall, and count of reachable correct
relationships beaten by their strongest competitor. Show aggregates by graph
density, family and withheld topology. Keep comparisons paired on identical
cases and explicitly separate logit sensitivity from graph-level improvement.

## Branch learning and gradient probes

Reconstruct the G2-0023 initial residual parameters from its fixed training
seed and compare their initial L2 norms with the saved contextual branch and
capacity-only branch (including the zero-initialized residual readout). Record
per-tensor update norms and maximum absolute movement. Compute read-only
`autograd.grad` sensitivities of summed output logits to the residual-branch
parameters on deterministic, predeclared case indices. These derivatives
measure *local sensitivity*, **not** historical training-gradient magnitude
or proof of whether earlier optimization actually updated each branch. No
loss/backward optimizer, label-derived gradient, or parameter mutation occurs.

G2-0023's cached capture uses PyTorch inference tensors; the audit clones
feature tensors outside inference mode before these gradient probes, so they
can safely participate in read-only autograd.

## Run

From `C:\Users\subsi\main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-context-utilization-audit --device cuda
```

Immutable default output:

`runtime/reports/gamer-v1/graph-g2-context-utilization-audit-0024/`

Files:

- `g2_context_utilization_audit_report.json`
- `g2_context_utilization_audit_per_case.jsonl.gz`

No weights are written; the existing G2-0023 weights, G1, ranker and frozen
backbone are not modified. If the G2-0023 artifacts reside elsewhere, pass
`--context23-run` to their directory. To run a distinct audit, pass a fresh
`--output` directory; overwriting an existing audit is prohibited.

## Interpretation

A near-zero `full_context` versus `residual_disabled` logit difference with
small saved readout weights means the new branch contributes almost nothing.
A substantial residual with very small `full_context` versus
`uniform_attention` differences suggests the residual is active but the
candidate-specific attention weights are not important. Material logit or
ranking changes without a graph-F1 benefit indicate useful or harmful signal
that the present decoder does not exploit consistently. These are
*development-only* diagnostics and cannot justify promotion by themselves.

## Safety / status

`model_selection=false`, `checkpoint_selection=false`,
`training_performed=false`, `production_promotion=false`,
`g2_certified=false`, `g2_production_unlocked=false`,
`certification_authorized=false`, `graph_execution_authorized=false`,
`memory_writes=false`, `sealed_test_used=false`,
`challenge_or_test_split_used=false`.

Local CPU tests cover full-forward equivalence, group locality, singleton
behavior, intervention-specific changes, gradient read-only behavior,
checkpoint verification, ledger/aggregate corruption rejection, deterministic
outputs and output immutability. Full CUDA replay needs the existing G2 runtime
artifacts and saved G2-0023 safetensors on the user's machine.
