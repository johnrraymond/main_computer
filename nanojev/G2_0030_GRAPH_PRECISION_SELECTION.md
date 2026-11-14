# G2-0030 — Graph-F1 Precision-Constrained Selection

## Why this experiment exists

G2-0029 trained three distinct, paper-inspired heads against the same frozen G2-0026 reference: predicate–argument scoring, dynamic span-graph message passing, and joint graph decoding. On the *new* G2-0029 development cohort, all three heads achieved higher recall and somewhat fewer ranking failures but **worse graph F1**, because their false-positive counts rose substantially. This patch tests whether **label-blind decision calibration and precision constraints** can retain the additional correct edges while rejecting their spurious competitors.

This is a controlled intervention on **graph selection**. It does **not** claim to solve the underlying language-representation problem, and it does not retrain any neural network. The previous G2-0029 experimental heads, rank head, G1 head, and backbone all remain frozen.

## Run

From `main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-graph-precision-selection --device cuda
```

Input: the existing G2-0029 report, 896-case compressed ledger, and complete trained checkpoint. No G2-0029 rerun is needed. G2-0029's default immutable directory is `runtime/reports/gamer-v1/graph-g2-architecture-bakeoff-0029/`. The normal G1/G2 prerequisite directories are also verified by their hashes.

New immutable output directory:

`runtime/reports/gamer-v1/graph-g2-graph-precision-selection-0030/`

Files:

- `g2_graph_precision_selection_report.json`: development comparisons, baseline controls, gates, confidence intervals and calibration audit.
- `g2_graph_precision_selection_per_case.jsonl.gz`: all 180 calibration rows and 896 DEV rows, with graph counts and ranking failures. No raw logits or gold labels are stored.
- `g2_graph_precision_selection_frozen_policies.json`: reproducible, frozen policy configurations. They reference G2-0029's *immutable* weights, not new trainable weights.

If an output directory exists, supply a new `--output`; the command never overwrites previous results.

## Strict parent and data protocol

1. Reconstruct and verify G2 ancestry through G2-0028 as in G2-0029. Verify G2-0029 report/ledger/weights SHA-256, every saved case's prompt identity, each per-split aggregate, the complete fresh/replay aggregates, and all predeclared development gates **before model capture**. Load all nine neural challengers with exact state keys, shapes and finite weights.
2. Generate **180 new TRAIN examples** from the established independent synthetic TRAIN curriculum, seed **460130**, excluding G2-0029's 640 training prompts, earlier development prompts, and the G2-0029 fresh development prompts. Do not introduce withheld topologies into calibration. Shuffle deterministically before splitting to avoid topology/density selection bias.
3. Use **120 TRAIN rows for graph-F1 policy fitting**, and **60 disjoint TRAIN rows for a calibration audit**. The audit measures generalization but **never selects a policy**. Neither stage retrains neural parameters.
4. For the reference and each of the nine trained challengers, search a **fixed, predeclared** small family of policies on TRAIN-fit only: base-score/challenger interpolation (`0.25, 0.50, 1.00`), decision thresholds (`.55, .65, .75, .82, .88, .93`), optional within-source/relation competitor margin (`none, 2.0, 4.0` logit units), and an optional minimum frozen-reference confirmation probability (`none, .60`). Every candidate keeps the existing global graph cap and allows **multiple correct destinations per source/relation**. The calibrated-reference baseline gets the same threshold and margin search, without challenger scores.
5. Select policy by **micro graph F1** on TRAIN-fit. On ties prefer fewer constraints, less departure from the frozen scorer, and the original threshold. **Do not select** architecture, seed, threshold, interpolation, model checkpoint, or decoder using DEV outcomes.
6. Replay 448 saved G2-0029 DEV prompts and verify every frozen G2-0026 graph count and competitor-ranking count. Evaluate **448 brand-new G2-0030 DEV prompts** (320 independent, seed **460230**, and 128 withheld-topology, seed **460231**). All candidate inventories use `train_gold=False`; no new candidates are injected from the authored graph.
7. Report graph precision, recall, F1, exact-graph rate, ranking failures, subgroup and topology breakdowns, candidate-oracle ceiling (offline label-informed only), frozen-reference versus recalibrated-reference comparisons, and paired case bootstrap intervals. Store frozen policies separately and hash all outputs.

## Predeclared success gate (development only)

An architecture passes only if **all three independent seeds** improve *fresh combined graph F1 by at least +0.02 absolute over **both*** the original G2-0026 reference and the newly calibrated G2-0026 reference; none worsens relationship-ranking failures; and none worsens withheld-topology graph F1 (compared with both references) or withheld-topology ranking failures (compared with frozen G2-0026). No seed or architecture can be cherry-picked from DEV after the run.

An architecture that only improves recall, ranking, or TRAIN-fit F1 **fails** unless it passes those actual graph-quality requirements. The purpose of the additional calibrated reference is to prevent a mere threshold adjustment from being falsely credited to architectural advances.

## Interpretation and fallback

If calibration does not clear these gates, this experiment **does not** justify another round of threshold searching on the same development set. We should stop adapting heads on the frozen feature interface and run a new comparison with learned occurrence/token-level representations and graph decoding. The G2-0029 bake-off *by itself* did not rule out those architectures, only the implementations constrained to the frozen interface.

The authored deterministic gold graph is an evaluation target and an offline inventory upper bound, **not a measured run of the original deterministic game solver**. True deterministic parity and end-to-end game-answer accuracy remain separate obligations.

No sealed/challenge/test access, checkpoint promotion, production mutation, graph execution, or G2 certification occurs in this patch, regardless of whether the development gate passes.
