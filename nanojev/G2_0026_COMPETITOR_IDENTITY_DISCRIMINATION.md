# G2-0026 — Competitor-Identity Discrimination

## Decision and scope

G2-0024 demonstrated that the G2-0023 context branch initially had negligible
influence on graph construction. G2-0025 subsequently established that branch-only
training can cause peer-dependent decision changes, without demonstrating a
ranking or graph-F1 advantage over a self-only, parameter-matched control.
G2-0026 tests whether **direct within-group, identity-sensitive supervision**
can make those learned peer interactions *useful*.

This is a development-only TRAIN intervention, not certification, sealed testing,
checkpoint selection, production promotion or graph-execution authorization.

## Predeclared treatment and control

- Both trained arms start from the same verified **G2-0023
  `relational_context_0023`** checkpoint. Its original source-conditioned base
  and all non-`rel_` parameters are frozen. Both residual readouts are reset
  to exactly zero. G1, the original rank head and the backbone are frozen.
- **`identity_self_0026`** uses candidate-local residual features, with no
  peer features in its new residual branch.
- **`identity_peer_0026`** uses candidate-specific attention over the other
  candidate destinations in the same **source/relation** group. The original
  scorer's preexisting leave-one-out conditioning is held constant in both arms.
- Identical optimizer, learning rate (0.0003), 480 steps, random draws,
  label-blind complete top-24 groups, initial weights and train labels. No
  extra trainable parameters are introduced in the peer arm. Both use the same
  G2-0019 precision-guard objective **plus the same** contrastive term.
- For each sampled source/relation group with both valid and invalid candidate
  destinations, contrast *each valid candidate* against up to the three
  **highest-scoring invalid destinations** using `softplus(1 + negative_logit
  - positive_logit)`. Average the comparisons within each group, average
  across mixed groups, multiply by 0.75, and add to the existing precision-
  guard training loss. Groups without mixed labels add no contrastive loss.
  Gold is used **only for TRAIN loss**. Multiple valid destinations remain
  simultaneously valid, and valid/valid comparisons never incur a penalty.
- Hard negatives come from the **same group**, never from different sources or
  relationships. The top-24 inventory, its group sampling, decoder cutoff
  (0.75), and graph interface remain fixed. Candidate ranking is not
  modified by evaluation gold.

## Parent evidence and controls

G2-0026 must verify the complete immutable G2-0025 report, compressed case
ledger and saved state tensor key inventory, including SHA-256 hashes,
predeclared parent protocol, 896 per-case records, all aggregate breakdowns,
and the original G2-0025 matched-model training metadata. It recreates all
three G2-0025 arms at their saved checkpoints and **replays** the 448 new
G2-0025 development examples to check both their graph metrics and their
hardest-negative ranking counts. An inconsistent parent aborts before new
results can be reported. The earlier G2-0016–0024 ancestry gates remain in
force.

Evaluated configurations:

1. `frozen_context_0023` — preserved frozen scorer.
2. `self_only_0025` — previous branch-only candidate-local control.
3. `peer_context_0025` — previous branch-only peer-attention control.
4. `identity_self_0026` — new supervised ranking objective, self-only.
5. `identity_peer_0026` — same new ranking objective, peer attention enabled.

The two TRAIN arms are compared against one another, not just the older frozen
reference. New prompt-SHA-disjoint development cohorts: 320 independent and
128 withheld-topology rows, generated with seeds 460032 and 460033. Replay of
448 previous rows is separate from fresh evaluation. **No DEV row may appear
in the 640-row training data** or be used for training or cutoff selection.

## Measurements and decisive gates

Each cohort and each density/family/topology split reports graph F1,
precision, recall, false positives, reachable gold, and valid relationships
ranking at or below the strongest invalid same-group competitor. The peer arm
is additionally evaluated with peer information suppressed, uniform
attention, competitor-value associations misaligned, and the entire residual
branch disabled. Logit shifts and **actual graph-decision flips** accompany
these counterfactuals.

The primary predeclared comparison requires the context arm's **new-DEV F1**
to exceed the identically trained self-only arm by at least 0.005 absolute F1
(0.5 percentage points) and to reduce hardest-negative ranking failures.
Other diagnostic gates compare against prior G2-0025 and frozen G2-0023,
measure response to competitor-identity perturbations, and require that
correct peer identity helps graph F1 rather than simply changing decisions.
These are diagnostic gates, **not automatic promotion gates**.

## Run

From `C:\Users\subsi\main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-competitor-identity-discrimination --device cuda
```

The run refuses preexisting output and writes immutable DEVELOPMENT artifacts
under `runtime/reports/gamer-v1/graph-g2-competitor-identity-discrimination-0026/`:

- `g2_competitor_identity_discrimination_report.json`
- `g2_competitor_identity_discrimination_per_case.jsonl.gz`
- `g2_competitor_identity_discrimination_development.safetensors`

Preserve all prior checkpoints and reports; the sealed test set remains
unopened, G2 remains uncertified and production weights remain unchanged.
