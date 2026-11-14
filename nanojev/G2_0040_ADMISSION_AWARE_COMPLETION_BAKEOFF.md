# G2-0040 — Admission-Aware Graph Completion Bake-Off

## Objective and causal premise

G2-0038 correctly admits additional G1 occurrence endpoints, but G2-0039
improved relationship F1 at the cost of previously exact graphs. The
G2-0039 independent case audit isolated losses in graphs for which the
contextual G1 selector admitted **no** new endpoints. This experiment first
protects that no-change path and then tests three competing mechanisms for
completion on a common expanded candidate inventory.

The goal is *additional exactly correct relationship graphs without sacrificing
previously exactly correct graphs*. Edge F1, ranking failures, and unfamiliar-
topology generalization are acceptance constraints, not substitute goals.

## Common protected baseline

1. Verify the immutable G2-0038 and G2-0039 parent checkpoints, parent
   policies, saved per-case identities, development aggregates and hashes.
2. Reuse the **TRAIN-enabled G2-0039 seed 460333** and its frozen
   75%-blended relationship scorer. Seeds 460331 and 460332 were disabled
   by G2-0039 TRAIN calibration; do not quietly activate them.
3. Run the G2-0038 contextual acceptor, frozen. If **no new occurrence anchor**
   is admitted, return the original G2 score set unchanged. Only when the
   candidate anchor set expands may the G2-0039 score blend be used.
4. Decode the admission-aware scores through frozen G2-0033 to produce the
   **common routed structured baseline**. All bake-off challengers compete
   against this **same** set, including the G2-0032 cardinality challenger.
5. For inputs with no admitted occurrences, every challenger must return the
   common baseline set verbatim; a completion network may not override it.

Admission counts, scorer routing and decoding are **label-blind**. No author
graph or development label is an inference input.

## Fixed competing completion strategies

The same candidate sets, scorer outputs, TRAIN data, and evaluation cases are
used for all strategies. No generalization claim is made on the basis of an
unmatched parent or a different set of inputs.

| Strategy | Change | Fixed policy grid |
|---|---|---|
| Cardinality | Frozen G2-0032 group-count recovery applied to routed scores | Three fixed edge-confidence, group-gap and budget configurations |
| Constrained | Frozen G2-0033 joint dynamic-programming graph-set decoder | Three fixed group/global count-energy weights |
| Adapted | Fine-tune a **copy** of the G2-0033 GraphSetEnergy decoder on original and recovered TRAIN examples; frozen G2-0031/32/38/39 | Same three fixed group/global count-energy weights |

No new high-level network replaces G2-0033; the adapted arm starts from its
original parameters and trains its existing count/set energy heads and edge
readout. G2-0032 is not retrained as part of this bake-off.

## Original v1 DATA separation and winner protocol (superseded by v2 below)

G2-0040 builds a fresh, disjoint, TRAIN-only synthetic-name pool with the
G2-0037 novelty generator and a new seed, excluding all earlier TRAIN and DEV
prompts. The bounded experimental budget is:

- 180 TRAIN fitting graphs, mixing both recovered and unexpanded graphs; the
  adapted decoder performs 160 optimization steps. There must be at least
  eight examples of each kind.
- 80 independent TRAIN-fit graphs for selecting the arm and configuration.
- 50 further disjoint TRAIN-audit graphs, **not** used to choose among winners.
- 320 new independent DEV and 128 unfamiliar-topology DEV graphs, along with
  replay of the 448 G2-0039 DEV graphs. These are used **only after** the
  TRAIN policy is frozen and only if a winner passes both TRAIN gates.

A candidate can advance from TRAIN-fit only if it produces at least one newly
exact graph, loses no previously exact graphs, and does not degrade micro-F1,
false positives or relationship ranking against the common baseline. It must
then preserve exact graphs, micro-F1, false positives and ranking on the
separate TRAIN-audit sample. The deterministic TRAIN-fit winner is chosen by
new exact graphs, F1 gain, and audited gains. The program records the full
nine-policy TRAIN-fit table and audit outcomes, including rejected candidates.

No safe TRAIN winner -> **stop before all DEV evaluation** and write immutable
TRAIN diagnostics. Never enable a strategy merely to force a development run.

## Independent acceptance and release controls

DEV acceptance requires a strictly positive number of newly exact graphs,
zero lost previously exact graphs against **both** routed and original
contextual decoders, nondegraded graph F1 and ranking, no regression on
unfamiliar topologies, and no lost previously exact graphs in the replayed
G2-0039 cohort. This is an **evaluation gate, not model selection**: a failed
DEV winner may not be replaced with a second arm chosen by looking at DEV.

All G1, backbone, G2-0031/32 parent models, G2-0033 parent models,
G2-0038 contextual acceptors, and G2-0039 relationship scorers remain
frozen. The sole trainable development artifact is a copied G2-0033 decoder.
No sealed tests, production promotions, or authorization changes occur.

## Original v1 operator command and output (superseded by v2 below)

Run from `nanojev`, after applying the repository-root-relative patch:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-completion-bakeoff --device cuda
```

Default immutable output:
`runtime/reports/gamer-v1/graph-g2-completion-bakeoff-0040/`

Output files are a JSON report, compressed per-case JSONL, the trained
G2-0033 development `.safetensors` weights, and frozen TRAIN-only policy
JSON. The full GPU experiment and any subsequent certification remain
separate from this source-code patch's unit-test acceptance.

## G2-0040 repair — opportunity-enriched bake-off (protocol v2)

The first CUDA run finished with `TRAIN_BAKEOFF_NO_SAFE_WINNER` and no
independent DEV evaluation. Its 180-case TRAIN capture contained only **17
expanded graphs**, so the completion alternatives did not receive a meaningful
sample of the graph changes they were intended to repair. Nine TRAIN-fit
configurations each produced **zero newly exact graphs** on 80 cases. This
failure does *not* establish an architectural ceiling.

The repaired version retains **the same three arms, fixed nine-policy grid,
frozen parents, source seed 460333 and TRAIN-only selection**. It changes
sampling, diagnostics and execution gating—not the model architecture:

1. Generate the same three separate, disjoint TRAIN pools before mining. Screen
   the first two pools with **label-blind** frozen G1/contextual G2-0038
   inference and the existing TRAIN-authorized G2-0038 admission policy.
   Only *after* admissions are decided, use TRAIN gold to categorize examples
   as anchor-rescuing, expanded-but-not-anchor-rescuing, or unchanged.
2. Mine a 180-case training cohort with **64 admitted/expanded graphs**, at
   least **12 newly anchor-representable graphs**, and 116 unchanged controls.
   Mine an 80-case TRAIN-fit cohort with **32 admitted graphs**, at least **6
   newly anchor-representable graphs**, and 48 unchanged controls. Sampling
   is deterministic and never crosses cohort boundaries. The miner prints
   screening cost and fails explicitly if pool opportunities are exhausted.
3. Keep an **unfiltered, naturally distributed 80-case TRAIN-audit** from the
   third independent TRAIN pool. It is not rebalanced or mined for admissions;
   it may veto a TRAIN-fit winner, but it cannot choose a replacement arm.
4. On all three TRAIN partitions, after label-blind G2 decoding, separately
   measure (a) gold endpoint coverage among admitted anchors and (b) **full
   gold relationship coverage in the actual frozen G2 shortlist**. Only the
   latter demonstrates that exact reconstruction is feasible for the decoder.
   Include counts of expanded representable/unrepresentable graphs, lost gold
   candidates, and potentially repairable graphs in the report.
5. Compare each strategy against the **same routed structured baseline**.
   Report newly exact, lost exact, micro-F1, false positives, ranking failures,
   and exact-graph transitions broken out by reachability. No secondary metric
   can substitute for a newly exact graph.
6. If TRAIN-fit contains **zero shortlist-representable, still-incorrect
   expanded graphs**, skip the 160-step fine-tuning altogether. Record the
   opportunity ceiling and all nine unevaluated configurations as a TRAIN-only
   diagnosis. Do not run DEV. If opportunities exist, run the unchanged
   three-way bake-off, freeze its TRAIN-fit winner, audit naturally, and only
   then evaluate independent DEV and withheld topologies.

The original immutable 0040 report is retained. The repaired command uses
**a new output directory** by default, preserving both runs:

`runtime/reports/gamer-v1/graph-g2-completion-bakeoff-0040-repair/`

The invocation is unchanged:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-completion-bakeoff --device cuda
```

The protocol is reported as `nanojev.gamer.graph.g2.admission-aware-completion-bakeoff.v2`.
TRAIN gold may determine what TRAIN examples are mined; it never changes G2
inference, thresholds, development selection, parent checkpoints, or a sealed
TEST cohort. As in the original run, failure to obtain a safe TRAIN-fit and
independent-audit winner prevents DEV evaluation and promotion.
