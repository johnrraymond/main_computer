# G2-0027 — Relationship Evidence Sufficiency Audit

## Motivation and governing question

G2-0023 added destination-specific contextual attention. G2-0024 demonstrated
little attention utilization. G2-0025 showed the attention branch can be trained
to change graph decisions, but contextual training did not beat an equal-capacity
self-only arm. G2-0026 added direct within-group identity-sensitive supervision;
the reported new-DEV results still did not establish a contextual advantage.

Before more training, determine **where the necessary information is lost**:
(1) frozen G1 failed to preserve the author-gold endpoints; (2) the frozen
compatibility inventory excluded an otherwise reachable relation; (3) top-24
label-blind candidate selection discarded a valid destination; or (4) a gold
edge survived but the frozen candidate evidence or learned scorer cannot
adequately distinguish it from competing incorrect destinations.

The fourth possibility is an *evidence sufficiency question*, not automatically
an optimization conclusion. This audit distinguishes direct evidence of exact
input collisions from descriptive feature-space proximity, which cannot on its
own establish information-theoretic insufficiency.

## Fixed inputs and lineage

The command is:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-relationship-evidence-sufficiency-audit --device cuda
```

It verifies the immutable G2-0016 through G2-0025 ancestors using their
existing validators, then verifies G2-0026's complete saved report, compressed
per-case ledger, frozen checkpoint weights, source-cohort hashes, train protocol
and aggregate reconstruction. It refuses mismatches. The previously held-out
G2-0026 DEV cohort is reconstructed and replayed in full against the saved
model decisions and competition counts. There is no reliance on console-paste
output. **The G2-0026 report, compressed ledger, and development weights must
exist at the normal local report path.**

Five frozen arms are scored without training: `frozen_context_0023`,
`self_only_0025`, `peer_context_0025`, `identity_self_0026`, and
`identity_peer_0026`. All share the same G1 node predictions, G2 rank logits,
source/relation top-24 inventory, fixed 0.75 logit-decoder protocol and graph
cap. Full prompt content is available to the frozen backbone for G1 capture,
but the **G2 candidate scorer does not directly receive the raw prompt text**;
it receives frozen vectors and engineered pair/relation context.

G2-0027 adds exactly **320 novel independent DEV examples**, seed **460034**,
and **128 novel OOD topology DEV examples**, seed **460035**. Every new prompt
must be distinct from all reconstructed previous TRAIN and DEV prompts, including
G2-0026. The sealed test/challenge splits are never opened.

## Measures and interpretation

1. **Gold-edge visibility:** Count exact author-marked gold edges whose endpoints
   survive frozen G1; among them, those surviving TRAIN-derived relation/type
   compatibility; among those, those entering the label-blind top-24 inventory.
   These produce nonoverlapping G1, compatibility, and inventory losses.
2. **Candidate-only hindsight ceiling:** Count gold edges remaining in the
   inventory, respecting the graph edge cap. Compute ideal F1 *assuming labels
   were known*. This is an offline upper bound for the fixed inventory, **not
   a deployable model or evidence of learnability**.
3. **Within-source/relation confusability:** For each reachable gold with one or
   more incorrect competing destinations from the **same source and relation**,
   compare normalized frozen destination-node vectors and the first four
   frozen feature blocks (source, destination, interaction, difference) against
   incorrect candidates. Report cosine similarities at predeclared thresholds
   0.95, 0.99 and 0.999. Retain raw means and counts; avoid threshold tuning.
4. **Exact interface conflicts:** Count opposite-label candidates only when
   the **complete frozen feature vector and original frozen base logit are both
   bit-for-bit identical**. This is a narrow but stronger test than approximate
   geometric similarity. The source/relation group is identical by construction.
5. **Surface ambiguity:** Count gold cases where a wrong same-group destination
   has identical case-folded prompt surface text, without claiming this exhausts
   the contextual evidence in the prompt or frozen vectors.
6. **Ranking correlation:** Count how many candidate-gold relationships fail
   to outrank the strongest negative under the saved G2-0026 peer and self-only
   arms, including how often those failures coincide with high pair cosine.
   These comparisons are evaluated **after** both scores and candidate selection
   have been computed without gold.

Aggregate separately by replay/new cohort, family, density and structural
topology. Include per-case coverage and ranking information and a bounded
hard-case frontier (up to five gold/negative pairs per case) with concise prompt
spans and coordinate references to enable further diagnosis.

## Critical limitations

- No finite set of similarity thresholds establishes semantic sufficiency or
  proves that the raw prompt lacks the information necessary to discriminate.
- Near-identical frozen embeddings are a **proxy for difficulty**, not a proof
  of contradictory labels given identical full inputs.
- An absence of exact collisions does **not** prove the frozen features are
  sufficient for successful prediction.
- The offline oracle ceiling uses author labels and never enters decoding,
  candidate ranking, training, or production inference.
- This experiment cannot establish the generalization of a hypothetical new
  decoder or determine whether a model could recover evidence lost upstream.

## Outputs and immutable contract

Written once under
`runtime/reports/gamer-v1/graph-g2-relationship-evidence-sufficiency-audit-0027/`:

- `g2_relationship_evidence_sufficiency_audit_report.json`
- `g2_relationship_evidence_sufficiency_audit_per_case.jsonl.gz`
- `g2_relationship_evidence_sufficiency_frontier.jsonl.gz`

All outputs include immutable ancestry hashes, a predeclared protocol and
explicit caution about interpretation. The old reports and weights are not
modified. This audit creates **no optimizer, no trainable parameters, no new
model weights, no checkpoint selection, no production promotion, no graph
execution, no memory writes, and no certification**. The sealed test is untouched.

## Decision logic after the audit

- If gold is predominantly missing at G1 or candidate-selection stages, work
  on that **upstream information bottleneck** before modifying the scorer.
- If genuinely identical same-group frozen inputs repeatedly require opposite
  labels, the frozen interface may be incapable of distinguishing those pairs;
  inspect the upstream representation and the source text, not another loss.
- If most gold is present, exact collisions are rare and nearest negatives are
  well separated, the evidence is *not shown to be insufficient*. A new
  representation-conditional probe would be required before attributing failure
  to the scorer, supervision, or optimization.
- In every case, **do not promote or certify based on DEV diagnostic findings**.
