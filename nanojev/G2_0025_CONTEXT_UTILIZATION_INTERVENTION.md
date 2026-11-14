# G2-0025 — Context utilization intervention (frozen trained scorer)

## Hypothesis

G2-0024 established that G2-0023's contextual residual contributes slightly
but does not use the identity of peer candidates meaningfully: uniform
attention produces no decision flips, peer-value misalignment changes only
four graph decisions, and removing the residual produces only a small F1
movement. This is a training-pathway question, not yet proof that candidate-
specific context is intrinsically useless.

**Objective:** isolate whether explicit optimizer access to the candidate-
specific residual, with the existing trained scorer frozen, can learn peer
information that improves graph decisions beyond a parameter-matched
candidate-local residual.

## Predeclared comparison

Both experimental models begin with an **identical copy of the original
G2-0023 `relational_context_0023` checkpoint**. The already trained scorer
(`pair`, source-conditioned layers, competition path, etc.) is held completely
frozen, as are G1, the G2 rank head, and the backbone. The extra residual
branch is unchanged architecturally from G2-0023. The only newly trainable
parameters are `rel_*`. To avoid inheriting the context arm's prior small
constant readout as an advantage, both copies reset the **residual readout
only** to zero. Their initial full predictions are consequently identical.

- `frozen_context_0023`: untouched parent reference (no new training).
- `self_only_0025`: branch receives candidate-local inputs, never other
  destinations. The residual parameters and optimizer are identical to the
  contextual arm.
- `peer_context_0025`: unchanged candidate-specific attention over the other
  members of the same source/relation group; no cross-group information.

Both training arms use the *same* 640 previously established TRAIN rows, the
same fixed seed/batch stream, 480 steps, AdamW learning rate 3e-4, clipping 1.0,
and the existing G2-0019 `precision_guard` loss. No new gold-based candidate
injection, threshold selection, architecture change, extra ranking weight,
single-destination softmax, or DEV-derived training occurs. The 0.75 decision
threshold, top-24 label-blind inventory and graph edge cap are unchanged.

## Parent and assessment contracts

Verify the full G2-0016—G2-0023 immutable ancestry and the G2-0023 checkpoint
shapes and weights. Verify **G2-0024's complete** diagnostic report and
compressed per-case ledger, including SHA-256, schema/flags, per-split identities,
predeclared interventions, and recomputed aggregate graph metrics. Replay
G2-0023 and G2-0024 parent full-context decisions exactly on the existing
448 G2-0023 DEV cases. Abort if any reported aggregate, checkpoint or decision
has drifted.

Primary assessment: **448 new prompt-disjoint DEV cases**, 320 independently
generated (seed 460030) and 128 withheld-topology (seed 460031), excluding all
prior TRAIN and DEV prompts through G2-0023. These are development diagnostics,
not sealed challenge/test or certification. Secondary assessment repeats the
previous 448 G2-0023 DEV cases without using their labels for optimization.

Assess micro graph F1, precision, recall, false positives, missing edges,
exact cases, and reachable gold; stratify by graph density, task family and
withheld topology. Count reachable correct relationships failing to outrank
their strongest incorrect competitor. On the trained contextual branch,
separately suppress peers, replace learned attention with uniform weights,
rotate peer values within each group, and disable the residual. Measure logits,
actual decision flips and graph F1. A score shift or gradient alone does not
establish useful competitor-specific learning. Diagnostic gates compare the
context arm both to the frozen parent and to the *trained self-only control*,
and require peer intervention sensitivity and F1 advantage over peer suppression
for evidence of useful contextual learning. They are **not** promotion gates.

## Run

From `C:\Users\subsi\main_computer\nanojev`:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-context-utilization-intervention --device cuda
```

Default immutable output:

`runtime/reports/gamer-v1/graph-g2-context-utilization-intervention-0025/`

Files:

- `g2_context_utilization_intervention_report.json`
- `g2_context_utilization_intervention_per_case.jsonl.gz`
- `g2_context_utilization_intervention_development.safetensors`

The saved weights are **development-only**. Output directories are never
overwritten; supply a new `--output` for a distinct run. The protocol locks
`--steps 480`, `--max-length 2048`, and the fixed training constants.

## Safety and test status

G1, rank head, source/backbone, all G2-0019–G2-0024 checkpoint files and
production inference remain unchanged. This experiment cannot certify G2,
unlock production, execute graph actions, select a checkpoint, or use the
sealed test/challenge split. Run CPU regression tests:

```powershell
python -m pytest -q nanojev/tests/test_nanojev_gamer_v1_g2_context_utilization_intervention.py
```

CUDA training and replay require the local Qwen adapter and historical runtime
reports/checkpoints and must run on the user's machine.
