# G2-0051 — Label-Blind Candidate Construction Repair

## Objective

Remove two candidate-availability bottlenecks from the research inference input:
(1) overly restrictive admission of frozen G1 alternate occurrences, and
(2) the per-source/relation top-destination truncation that precedes graph
selection. All new candidates are derived from frozen G1 proposal evidence and
relation/kind signatures, **not from author graph targets**.

## Implemented constructor

`tools.nanojev_gamer_v1_g2_candidate_construction_repair` exposes:

- `expand_occurrences(evidence, admitted)`: preserve the selected G1 anchors;
  add frozen G1 positive-margin alternatives in a bounded kind-balanced order.
- `inventory(anchors, allowed)`: enumerate *all* directed, type-compatible
  relationship candidates, retaining multiple destinations per source.
- `inventory(..., open_types=True)`: a deliberately permissive diagnostic
  ceiling for unknown signature restrictions. This is **not** an approved
  model policy and should not be promoted as a permanent semantic type grammar.
- `candidates_for_record(...)`: monotone reference, unbounded, recovered-typed,
  and recovered-open inventories. It cannot read labels to create candidates.

The optional `capture_candidate_repair_rows` hook reuses the original G2-0040
frozen Qwen/G1/relationship-model context. It is restricted to author-marked
TRAIN-domain counterfactual rows. G2-0050's existing run and production route
remain unchanged.

## Acceptance and causal localization

G2-0051 recreates the **same G2-0050 counterfactual pairs** and measures:

1. `legacy`: original G2-0050 shortlisted candidate inventory.
2. `unbounded_typed`: existing selected G1 endpoints, no destination top-k.
3. `recovered_typed`: frozen G1 alternate endpoints, no destination top-k.
4. `recovered_open`: same expanded endpoints, exploratory open relation kinds.

After all four inventories exist for a prompt, the author graph is used *only*
for auditing. The report separates missing G1 catalog endpoints, unadmitted
G1 endpoints, relation-kind restrictions, and legacy ranking/shortlist loss.
The probe must reproduce the exact G2-0050 prompt identities and immutable
per-prompt ledger; identity or ledger corruption refuses the run. Candidate
replay is then compared prompt by prompt, including missing-gold-edge counts.
If the same prompts produce different legacy inventories, the run still saves
the within-run coverage measurements and a detailed `parent50_replay_diagnostic`
but disallows a confirmed coverage signal and directs investigation of the
replay drift. G2-0050 reported 7/32 graphs and 1/16 complete pairs.

The predeclared engineering signal is >=31/32 fully reachable probe graphs
and >=15/16 fully reachable probe pairs **in the recovered-typed inventory**.
Passing this signal permits a separate scoring/training integration effort; it
is not graph reconstruction, generalization, certification, or promotion.

## Command

From `nanojev` after applying the patch at `main_computer` repository root:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-candidate-construction-repair --device cuda
```

Full report: `runtime/reports/gamer-v1/graph-g2-candidate-construction-repair-0051/`.

## Explicit limitations

This patch creates a real high-recall candidate **inventory**, not a trained
scorer capable of ranking the additional candidates. The frozen G2-0033
set/cardinality decoder still expects <=24 candidates per source/relation group;
that decoder is not silently widened. Candidate *coverage* must be shown to
improve before a subsequent score-and-decode integration is attempted.
No DEV/TEST examples or 0050 parent model weights are altered.
