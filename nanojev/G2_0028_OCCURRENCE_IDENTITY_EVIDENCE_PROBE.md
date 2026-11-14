# G2-0028 — Occurrence-Identity Evidence Probe

G2-0027 found that many correct relationship candidates survive G1 and the label-blind top-24 inventory yet rank below incorrect same-source/relation competitors. Repeated destination surface strings and highly similar frozen pair vectors are common. These observations do not establish whether useful occurrence evidence is absent or merely unexploited.

## Protocol

Run from the NanoJev directory:

```powershell
& $py -u -m tools.nanojev_gamer_v1_graph_curriculum frob-g2-occurrence-identity-evidence-probe --device cuda
```

This experiment verifies the previous G2 ancestry, including G2-0027's report, full compressed ledger, frontier hashes and recomputed aggregates. It uses the existing 640 TRAIN examples, replays the 448 prior G2-0027 DEV cases and evaluates 448 fresh DEV examples (320 independent, seed 460036, plus 128 unseen-topology, seed 460037). The new prompts are checked for overlap with previously generated TRAIN and DEV examples.

All five arms use an identical two-layer, 96-hidden-unit per-candidate classifier, the same initialization, identical feature dimensionality, same TRAIN examples, exactly the same optimization batches and multi-positive BCE-plus-ranking loss, and 600 fixed training steps. Optional input slots are zero-filled in controls, so parameter counts are equal.

1. `frozen_features`: Existing frozen G1 node vectors, G2 relation/pair features and base margin, with zero optional input slots.
2. `occurrence_position`: Frozen features plus absolute source/destination span offsets, relative offsets and repeated-surface occurrence ordinal/count features.
3. `local_structure`: Frozen features plus index and immediate-neighbor gaps among **predicted** nodes, local same-type density and source/destination proximity. No author/gold graph edges are used as structural features.
4. `position_and_structure`: Both extra information channels.
5. `shuffled_evidence_control`: Both channels are deterministically shuffled between candidates *inside the same source/relation group*. This preserves feature marginals but breaks candidate/occurrence alignment without inspecting gold labels.

`identity_peer_0026` is included as an immutable frozen scoring reference. It is not trained by this experiment. Candidate selection uses `make_inventory(..., train_gold=False)` even on TRAIN: gold labels never force candidate inclusion. TRAIN labels are consulted only after candidate capture for mixed-group sampling and training losses. No DEV data enters training, checkpoint selection or threshold fitting.

Primary diagnostics are correct-versus-incorrect ranking failures within source/relation groups, pairwise win rate, and errors among repeated-surface or >=0.99 cosine-similar competitors. Graph precision/recall/F1 at an *untuned* 0.5 probe cutoff are secondary; the G2-0026 reference uses its original 0.75 cutoff and its F1 is not directly a calibrated comparison. Predeclared paired case-bootstrap intervals quantify uncertainty for the combined probe versus frozen-only and shuffled controls.

## Outputs

`runtime/reports/gamer-v1/graph-g2-occurrence-identity-evidence-probe-0028/`

- `g2_occurrence_identity_evidence_probe_report.json`
- `g2_occurrence_identity_evidence_probe_per_case.jsonl.gz`
- `g2_occurrence_identity_evidence_probe_diagnostic.safetensors`

Only isolated **diagnostic probe** weights are saved. The backbone, G1 head, G2 rank head, prior G2 checkpoint weights, graph execution and production routing remain unchanged. Sealed and challenge/test data remain untouched; G2 remains uncertified.

## Interpretive limits

An improvement over matched frozen-only and shuffled controls would show that explicit occurrence/locality evidence is recoverable by this particular probe under these TRAIN/DEV conditions. It would not establish that existing frozen features lack an equivalent signal, nor certify a production architecture. Conversely, probe failure is not proof of information absence: limited capacity, optimization, sampling and representation all remain plausible explanations. Do not use DEV findings to tune or promote a production decoder.
