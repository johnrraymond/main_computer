# G2-0059 — Remaining Endpoint Coverage: Offline Diagnosis + Bounded Replay

This is a research-only extension of the **existing G2-0059 spatial pilot**. It
must not overwrite or promote the original 3/16 shard, the spatial 12/16 shard,
the G1/G2-0052 frozen weights, or the existing 98% admission gate.

## Known CUDA evidence

The earlier exact-pair spatial replay improved candidate reachability from 3/16
to **12/16** and missing gold edges from 41 to **8**, with remaining missing
edges in pairs 4, 7, 8, 12. Of these eight, six were `measures`, one was
`refers_to`, and one was `applies_to`; six belonged to inserted distractors,
two to original graph edges. The post-capture audit identifies missing
endpoints; it does **not** by itself prove whether a frozen G1 score, token
boundary or 192-anchor budget caused any individual miss.

## Step 1 — Inspect the existing spatial shard (no CUDA, no mutation)

From `C:\Users\subsi\main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale inspect-binding --capture ..\runtime\reports\gamer-v1\graph-g2-0059-spatial-pilot
```

This reads only checksum-verified TRAIN sidecars. Every missing edge is reported
with its source and destination spans, surface, kind, relation, raw neural
margin, and token-boundary reason. Existing sidecars predate the newly added
`raw_kind_rank` field; the new CUDA replay includes that rank for any errors
that remain. No sealed test labels are opened.

## Step 2 — Replay *the same 16 pairs* under a separate evidence policy

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-0059-scale capture --device cuda --max-shards 1 --candidate-policy evidence --output ..\runtime\reports\gamer-v1\graph-g2-0059-evidence-pilot --compare-capture ..\runtime\reports\gamer-v1\graph-g2-0059-spatial-pilot
```

The evidence policy uses only inference prompt text, frozen raw G1 candidate
spans/scores, allowed type signatures and the already admitted 0051 anchors.
It proposes (a) punctuation-normalized standalone numeric mentions and (b)
repeated action/actor surfaces that originate from frozen G1 hypotheses.
These compete for the unfilled portion of the **unchanged 192-anchor budget**;
remaining slots are filled from the spatial G1 alternative. It never reads
`graph`, `object_spans`, `expected_decisions`, or gold edges when selecting
anchors. The semantic status of lexical numeric proposals is a *hypothesis*;
a final graph edge is still a prediction, not a hard-coded label.

The candidate inventory is frozen before the author gold is accessed for the
comparison. The saved `pilot-comparison.json` includes exact original-vs-new
missing edge IDs, recovered edges, introduced regressions, and persistent
misses. It refuses a policy mismatch (e.g., evidence compared against legacy
instead of spatial), pair drift, parent-sidecar drift, or corrupt shards.

## Gate and decision

Expected target for the original 16 pairs is:

- `baseline_reachable_pairs = 12`
- `baseline_missing_edges = 8`
- `candidate_reachable_pairs = 16`
- `candidate_missing_edges = 0`
- `regressed_edges = 0`
- `pilot_reachability_floor_passed = true`

These are **acceptance targets**, not claimed outcomes. If any fail, inspect
the new `pilot-comparison.json` and `inspect-binding` the evidence pilot,
then repair the demonstrated remaining mechanism. No broad capture or model
training is authorized by this pilot alone; validation and challenge splits
must still independently satisfy the original data-coverage gates.

## Verification boundary

CPU tests exercise label-blind candidate construction, including a low-scoring
numeric marker attached to a period and repeated action occurrences; source
text coverage for the original 16 authored pairs; exact-pair checkpoint and
sidecar comparison; capture isolation; and corruption/policy refusal. A mocked
CPU capture verifies the *pipeline contract* but **does not measure actual
Qwen/G1 reachability**. Full frozen CUDA replay must be run by the operator.
