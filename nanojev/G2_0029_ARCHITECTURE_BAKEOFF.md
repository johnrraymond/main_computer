# G2-0029 — Literature-Grounded Architecture Bake-Off

## Objective

Stop extending the same candidate-scoring architecture without a performance gain. Test distinct graph-construction mechanisms, all initialized as **frozen G2-0026 residual challengers**, against the very same label-blind G1/G2 candidate inventories. The evaluation question is whether a new architecture reliably improves *actual graph construction*, rather than probe loss or attention activity.

The prior G2-0027 audit found that nearly 90% of gold relationships enter G2, but the candidate scorer often ranks wrong alternatives higher. G2-0028 showed useful locality signal in weak, separate diagnostic probes, without establishing gain over the strong G2 scorer.

## Published starting points

This experiment uses three *paper-inspired prototypes*, not reproduced published implementations:

1. **Predicate–argument biaffine role scoring** (`predicate_argument`): role-conditioned source and destination projections and a biaffine compatibility score. Inspired by Cai et al., *A Full End-to-End Semantic Role Labeler, Syntactic-agnostic Over Syntactic-aware?* (COLING 2018): https://aclanthology.org/C18-1233/
2. **Dynamic span-graph refinement** (`dynamic_span_graph`): two rounds of message passing among frozen G1-predicted node occurrences connected by label-blind candidate edges, then an edge readout. Inspired by Wadden et al., *Entity, Relation, and Event Extraction with Contextualized Span Representations* (EMNLP 2019): https://aclanthology.org/D19-1585/
3. **Joint structured graph decoding** (`joint_graph_decoder`): learned edge residuals plus candidate-global destination features and a learned, variable-cardinality source/relation subset decoder. Multiple correct destinations remain legal. Inspired by Lin et al., *A Joint Neural Model for Information Extraction with Global Features* (ACL 2020): https://aclanthology.org/2020.acl-main.713/

For held-out compositional evaluation design, see Kim and Linzen, *COGS: A Compositional Generalization Challenge Based on Semantic Interpretation* (EMNLP 2020): https://aclanthology.org/2020.emnlp-main.731/

**The joint decoder is not the published OneIE beam-search implementation.** It incorporates graph-global destination evidence and optimizes a nonexclusive, group-structured selection objective. The dynamic graph is not the complete DyGIE++ architecture, and the biaffine scorer does not implement complete semantic role labeling. They are independently executable alternative mechanisms in NanoJev's current interface, with these distinctions recorded in the report.

## Run

From `main_computer\\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-architecture-bakeoff --device cuda
```

Default immutable output directory:

`runtime/reports/gamer-v1/graph-g2-architecture-bakeoff-0029/`

It writes:

- `g2_architecture_bakeoff_report.json`
- `g2_architecture_bakeoff_per_case.jsonl.gz`
- `g2_architecture_bakeoff_development.safetensors`

A second run must use a fresh `--output`; it will not overwrite the first one.

## Fixed protocol

- Verify the full G2 ancestry and the G2-0028 report, its compressed ledger (all 896 cases), trained diagnostic weights, and reproduced development aggregates before any optimizer is created.
- TRAIN uses the established 640 examples, and the ordinary frozen G1/G2 top-24 candidate inventory with `train_gold=False` for **every** arm. Supervision is used only *after* inventory creation to select training groups and compute loss.
- All models start from the exact frozen G2-0026 reference logits. Backbone, G1, frozen G2 rank head, and parent G2 checkpoints do not change.
- Every architecture gets the same frozen source/destination representations, base scores, occurrence-position features, and predicted-node locality features. Architectures differ in computation and decoding, not in privileged access to evidence.
- Three predeclared seeds: 460038, 460039, 460040; **480 optimizer steps per seed**, AdamW learning rate 0.00008. Same source TRAIN cases, TRAIN sampling indices, and local BCE-plus-multi-positive contrastive supervision. The structured-decoder arm additionally learns a TRAIN-only cardinality factor; different parameter counts and training objectives are explicitly recorded rather than falsely described as capacity-matched.
- TRAIN group sampling includes groups with multiple valid destinations and negative-only groups; positives are **not** exclusive.
- Evaluate the 448 saved G2-0028 DEV cases (replay) and **448 new, nonoverlapping G2-0029 DEV cases**: 320 independent prompts (seed 460038) plus 128 withheld-topology prompts (seed 460039).
- All scoring and decoding thresholds fixed before DEV: `0.75` sigmoid probability (= logit ~1.0986). Structured cardinality selection is learned on TRAIN only. No DEV tuning, no checkpoint selection, and no sealed/challenge/test use.
- A frozen `identity_peer_0026` baseline receives identical candidates. The metrics include graph precision, recall, F1, exact-case rate, ranking failures, same-surface and cosine>=0.99 errors, novel-topology breakdowns, and 500-draw paired case bootstraps for each seed.

## Predeclared success gate

A candidate *passes the development gate* only if **all three seeds** improve combined fresh-DEV graph F1 by **at least +0.02 absolute**, and none of the seeds worsens overall ranking failures, withheld-topology F1, or withheld-topology ranking failures against the same frozen G2-0026 reference. If the gate fails, report failure and retain all experimental weights without promotion. Passing this development gate is *not* certification.

## Deterministic comparison: what this run can and cannot measure

The synthetic prompt generator already provides authored gold object and relationship graphs. The ledger computes an **offline candidate-inventory oracle ceiling** using those labels, solely to measure how much of the deterministic target is reachable from the frozen G1/G2 inventory. That upper bound is *not* the old deterministic executable's measured accuracy; no existing deterministic solver is executed by G2-0029. The report explicitly records this limitation.

A true apples-to-apples comparison to the original deterministic solver's graph assembly and final game answers needs a separate, executable baseline with aligned input/output definitions. Do not equate the gold graph upper bound with end-to-end deterministic solver parity.

## Explicitly outside this patch

- Direct pretrained **text-to-graph sequence generation** (UIE / seq2seq) is not implemented here. It requires a different encoder-decoder interface and a resource feasibility gate rather than falsely calling a candidate-pointer network text-to-graph generation.
- Frozen G1's missed gold nodes and top-24 inventory omissions remain unrecoverable by all three challengers. The bake-off primarily isolates G2 graph construction **conditional on candidate visibility**.
- No production integration, deployment, checkpoint promotion, graph execution, G2 certification, or sealed-test unsealing. This run creates diagnostic development checkpoints only.

## Decision after results

Do not rationalize marginal differences. Report each seed separately, include the hard failures, and retain the frozen original. If a challenger clears the gate, independently test it on a genuinely withheld evaluation split under a separately authorized certification protocol before considering promotion. If all fail, stop extending this frozen G2 interface; consider joint span detection and truly autoregressive text-to-graph parsing as deeper replacements rather than another local attention modification.
